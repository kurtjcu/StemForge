"""Tests for multi-GPU scheduler in pipeline_manager.

All tests mock torch.cuda — no real GPUs needed.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import torch

from backend.services.pipeline_manager import (
    GpuContext,
    GpuScheduler,
    GpuSlot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_scheduler(
    num_gpus: int,
    excluded: set[int] | None = None,
    free_vram: dict[int, int] | None = None,
) -> GpuScheduler:
    """Create a scheduler with mocked GPU state."""
    excluded = excluded or set()
    free_vram = free_vram or {}
    default_free = 8 << 30  # 8 GiB
    default_total = 16 << 30

    sched = GpuScheduler()

    # Patch _do_init so it doesn't import acestep_state
    def _mock_init():
        sched._excluded = excluded
        if num_gpus == 0:
            return
        for i in range(num_gpus):
            if i in excluded:
                continue
            sched._slots.append(GpuSlot(index=i))

    with patch.object(sched, "_do_init", _mock_init):
        sched._ensure_init()

    # Patch torch.cuda calls used by _pick_slot
    return sched


@pytest.fixture
def single_gpu():
    return _make_scheduler(1)


@pytest.fixture
def dual_gpu():
    return _make_scheduler(2)


@pytest.fixture
def no_gpu():
    return _make_scheduler(0)


# ---------------------------------------------------------------------------
# 1. Single GPU serialization
# ---------------------------------------------------------------------------

def test_single_gpu_serialization():
    """With 1 GPU, two concurrent sessions serialize."""
    sched = _make_scheduler(1)
    order: list[str] = []
    barrier = threading.Event()

    def first():
        with sched.session() as ctx:
            assert ctx.gpu_index == 0
            order.append("first-start")
            barrier.set()
            time.sleep(0.1)
            order.append("first-end")

    def second():
        barrier.wait()
        time.sleep(0.02)  # ensure first has the lock
        with sched.session() as ctx:
            assert ctx.gpu_index == 0
            order.append("second-start")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert order == ["first-start", "first-end", "second-start"]


# ---------------------------------------------------------------------------
# 2. Dual GPU concurrency
# ---------------------------------------------------------------------------

def test_dual_gpu_concurrency():
    """With 2 GPUs, two sessions run concurrently on different GPUs."""
    sched = _make_scheduler(2)
    gpu_indices: list[int] = []
    lock = threading.Lock()
    both_running = threading.Barrier(2, timeout=5)

    def mock_mem_get_info(idx):
        return (8 << 30, 16 << 30)

    def worker():
        with sched.session() as ctx:
            with lock:
                gpu_indices.append(ctx.gpu_index)
            both_running.wait()  # proves both are inside session simultaneously

    with patch("torch.cuda.mem_get_info", side_effect=mock_mem_get_info):
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert len(set(gpu_indices)) == 2, "Should use different GPUs"


# ---------------------------------------------------------------------------
# 3. Five users, two GPUs
# ---------------------------------------------------------------------------

def test_five_users_two_gpus():
    """5 threads submit jobs; at most 2 run concurrently; all 5 complete."""
    sched = _make_scheduler(2)
    active = threading.Semaphore(0)
    max_concurrent = 0
    current = 0
    lock = threading.Lock()
    completed = []

    def worker(n):
        nonlocal max_concurrent, current
        with sched.session() as ctx:
            with lock:
                current += 1
                if current > max_concurrent:
                    max_concurrent = current
            time.sleep(0.05)
            completed.append(n)
            with lock:
                current -= 1

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(completed) == 5
    assert max_concurrent <= 2


# ---------------------------------------------------------------------------
# 4. AceStep exclusion
# ---------------------------------------------------------------------------

def test_acestep_exclusion():
    """3 GPUs, AceStep on GPU 1 — scheduler only offers 0 and 2."""
    sched = _make_scheduler(3, excluded={1})

    assert sched.slot_count == 2
    indices = {s.index for s in sched._slots}
    assert indices == {0, 2}
    assert 1 in sched.excluded_indices


# ---------------------------------------------------------------------------
# 5. Pipeline affinity
# ---------------------------------------------------------------------------

def test_pipeline_affinity():
    """Second call with same pipeline_hint prefers the GPU that has it cached."""
    sched = _make_scheduler(2)

    # Seed GPU 1's cache with "demucs"
    sched._slots[1].pipelines["demucs"] = MagicMock()

    with sched.session(pipeline_hint="demucs") as ctx:
        assert ctx.gpu_index == 1, "Should prefer GPU with cached pipeline"


# ---------------------------------------------------------------------------
# 6. All GPUs busy — blocks
# ---------------------------------------------------------------------------

def test_all_gpus_busy_blocks():
    """When all GPUs are acquired, a third caller blocks until one releases."""
    sched = _make_scheduler(2)
    blocked = threading.Event()
    released = threading.Event()

    def hold_gpu(slot_idx):
        with sched.session():
            blocked.set()
            released.wait(timeout=5)

    # Acquire both GPUs
    t1 = threading.Thread(target=hold_gpu, args=(0,))
    t2 = threading.Thread(target=hold_gpu, args=(1,))
    t1.start()
    time.sleep(0.02)
    t2.start()
    time.sleep(0.05)

    got_slot = threading.Event()

    def third():
        with sched.session() as ctx:
            got_slot.set()

    t3 = threading.Thread(target=third)
    t3.start()

    # Third should be blocked
    assert not got_slot.wait(0.1), "Should be blocked"

    # Release one
    released.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    got_slot.wait(timeout=5)
    assert got_slot.is_set(), "Should have acquired after release"
    t3.join(timeout=5)


# ---------------------------------------------------------------------------
# 7. Per-GPU eviction
# ---------------------------------------------------------------------------

def test_per_gpu_eviction():
    """Evict from GPU 0 leaves GPU 1's cache untouched."""
    sched = _make_scheduler(2)

    mock0 = MagicMock()
    mock1 = MagicMock()
    sched._slots[0].pipelines["demucs"] = mock0
    sched._slots[1].pipelines["demucs"] = mock1

    # Evict only GPU 0
    cache0 = sched.get_pipeline_cache(0)
    pipeline = cache0.pop("demucs", None)
    if pipeline:
        pipeline.clear()

    assert "demucs" not in sched._slots[0].pipelines
    assert "demucs" in sched._slots[1].pipelines
    mock0.clear.assert_called_once()
    mock1.clear.assert_not_called()


# ---------------------------------------------------------------------------
# 8. CPU fallback
# ---------------------------------------------------------------------------

def test_cpu_fallback():
    """No CUDA → GpuContext(gpu_index=None, device=cpu), single lock serializes."""
    sched = _make_scheduler(0)

    with patch("utils.device.get_device", return_value=torch.device("cpu")):
        with sched.session() as ctx:
            assert ctx.gpu_index is None
            assert ctx.device == torch.device("cpu")


# ---------------------------------------------------------------------------
# 9. VRAM preference
# ---------------------------------------------------------------------------

def test_vram_preference():
    """2 GPUs with different free VRAM — scheduler picks the one with more."""
    sched = _make_scheduler(2)

    # Mock mem_get_info: GPU 0 has 4 GiB free, GPU 1 has 12 GiB free
    def mock_mem_get_info(idx):
        if idx == 0:
            return (4 << 30, 16 << 30)
        return (12 << 30, 16 << 30)

    with patch("torch.cuda.mem_get_info", side_effect=mock_mem_get_info):
        with sched.session() as ctx:
            assert ctx.gpu_index == 1, "Should prefer GPU with more free VRAM"


# ---------------------------------------------------------------------------
# 10. Session isolation
# ---------------------------------------------------------------------------

def test_session_isolation():
    """5 concurrent sessions get independent GpuContext instances."""
    sched = _make_scheduler(2)
    contexts: list[GpuContext] = []
    lock = threading.Lock()

    def worker():
        with sched.session() as ctx:
            with lock:
                contexts.append(ctx)
            time.sleep(0.05)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(contexts) == 5
    # Each context is its own object (not shared)
    assert len(set(id(c) for c in contexts)) == 5
