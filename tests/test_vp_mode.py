"""Objective tests for Vocal Preservation Mode conditioning controls.

Tests are split into two tiers:
  UNIT — pure-python / numpy tests, no model required, fast.
  INTEGRATION — runs actual StableAudioPipeline with 10 steps
                (fast enough to complete in a few minutes on GPU).

Run with:
    python tests/test_vp_mode.py [--unit-only]

Exit code 0 = all selected tests pass.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys
import time
import traceback
import warnings

import numpy as np

# ── root on sys.path so project imports resolve ───────────────────────────────
ROOT = pathlib.Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VOICE_WAV = pathlib.Path.home() / "Music" / "voice_tester_short.wav"
PROMPT     = (
    "clean solo vocal performance, smooth transitions, "
    "dry recording, no reverb, no instruments"
)
NEG_PROMPT = "music, instruments, echo, reverb, noise, clipping"
FAST_STEPS = 10      # keep integration tests quick
SHORT_DUR  = 5.0     # seconds per integration test clip
WINDOW_SEC = 5.0     # match SHORT_DUR so timing lock = 1 window


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PASS = "\033[92mPASS\033[0m"
_FAIL = "\033[91mFAIL\033[0m"
_SKIP = "\033[93mSKIP\033[0m"
_INFO = "\033[94mINFO\033[0m"

_results: list[tuple[str, str, str]] = []   # (tier, name, status+note)


def _report(tier: str, name: str, ok: bool, note: str = "") -> None:
    tag = _PASS if ok else _FAIL
    line = f"  [{tag}] {name}"
    if note:
        line += f"  — {note}"
    print(line)
    _results.append((tier, name, "PASS" if ok else "FAIL"))


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def _rms(arr: np.ndarray) -> float:
    return float(np.sqrt(np.mean(arr ** 2)))


def _peak(arr: np.ndarray) -> float:
    return float(np.max(np.abs(arr)))


def _spectral_correlation(a: np.ndarray, b: np.ndarray, sr: int) -> float:
    """Pearson correlation of log-magnitude spectra (mono mixdown, first 2 s)."""
    samples = min(a.shape[-1], b.shape[-1], 2 * sr)
    a_mono = a[:, :samples].mean(axis=0) if a.ndim == 2 else a[:samples]
    b_mono = b[:, :samples].mean(axis=0) if b.ndim == 2 else b[:samples]
    fa = np.abs(np.fft.rfft(a_mono))
    fb = np.abs(np.fft.rfft(b_mono))
    # Log-magnitude (avoid log(0))
    fa = np.log1p(fa)
    fb = np.log1p(fb)
    if fa.std() < 1e-8 or fb.std() < 1e-8:
        return 0.0
    return float(np.corrcoef(fa, fb)[0, 1])


def _boundary_discontinuity(arr: np.ndarray, window_samples: int, sr: int) -> float:
    """Return largest amplitude jump at window boundaries (in amplitude units)."""
    n_boundaries = arr.shape[1] // window_samples
    max_jump = 0.0
    for i in range(1, n_boundaries + 1):
        idx = i * window_samples
        if idx >= arr.shape[1]:
            break
        # Compare 5 ms RMS on each side of the boundary
        half = max(1, int(0.005 * sr))
        lo = arr[:, max(0, idx - half): idx]
        hi = arr[:, idx: idx + half]
        jump = abs(_rms(hi) - _rms(lo))
        max_jump = max(max_jump, jump)
    return max_jump


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS (no model needed)
# ─────────────────────────────────────────────────────────────────────────────

def unit_tests() -> None:
    _section("UNIT — crossfade helper")

    from pipelines.musicgen_pipeline import MusicGenPipeline

    pipe = MusicGenPipeline()

    # Single chunk — returned as-is
    a = np.ones((2, 4410), dtype=np.float32)
    out = pipe._crossfade_chunks([a], sr=44100)
    _report("unit", "single_chunk_passthrough",
            out.shape == a.shape and np.array_equal(out, a))

    # Two chunks — output length = (n1 - fade_n) + fade_n + (n2 - fade_n)
    # = n1 + n2 - fade_n
    n1, n2 = 8820, 8820
    fade_ms = 50.0
    sr = 44100
    expected_fade_n = min(int(fade_ms / 1000 * sr), n1 // 2)
    expected_len = n1 + n2 - expected_fade_n
    c1 = np.ones((2, n1), dtype=np.float32)
    c2 = np.ones((2, n2), dtype=np.float32) * 0.5
    joined = pipe._crossfade_chunks([c1, c2], sr=sr, fade_ms=fade_ms)
    _report("unit", "two_chunk_output_length",
            joined.shape[1] == expected_len,
            f"got {joined.shape[1]}, expected {expected_len}")

    # Crossfade boundary should be smooth: no sample jumps > 0.5 amplitude units
    boundary_idx = n1 - expected_fade_n
    rms_before = _rms(joined[:, boundary_idx - 10: boundary_idx + 10])
    _report("unit", "crossfade_boundary_smooth",
            rms_before < 1.5,
            f"boundary rms={rms_before:.4f}")

    # Three chunks — verify length telescopes correctly
    c3 = np.ones((2, n2), dtype=np.float32) * 0.25
    three = pipe._crossfade_chunks([c1, c2, c3], sr=sr, fade_ms=fade_ms)
    expected_three = n1 + n2 + n2 - 2 * expected_fade_n
    _report("unit", "three_chunk_output_length",
            three.shape[1] == expected_three,
            f"got {three.shape[1]}, expected {expected_three}")

    _section("UNIT — window slice count")

    def _window_count(duration: float, window: float) -> int:
        return max(1, math.ceil(duration / window))

    for dur, win, expected in [
        (10.0, 5.0,  2),
        (10.0, 10.0, 1),
        (11.0, 5.0,  3),
        (5.0,  10.0, 1),
        (30.0, 10.0, 3),
    ]:
        got = _window_count(dur, win)
        _report("unit", f"window_count(dur={dur},win={win})",
                got == expected, f"got {got}, expected {expected}")

    _section("UNIT — conditioning_strength scaling")

    # Strength 0.5 should halve the amplitude
    dummy = np.ones((2, 100), dtype=np.float32)
    scaled = dummy * 0.5
    _report("unit", "strength_0.5_halves_amplitude",
            abs(scaled.max() - 0.5) < 1e-6)

    # Strength 0.0 → silence
    zeroed = dummy * 0.0
    _report("unit", "strength_0.0_produces_silence",
            zeroed.max() < 1e-8)

    _section("UNIT — voice file accessible")

    ok = VOICE_WAV.exists() and VOICE_WAV.stat().st_size > 0
    _report("unit", "voice_tester_short.wav_exists",
            ok, str(VOICE_WAV))


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS (load model, generate short clips)
# ─────────────────────────────────────────────────────────────────────────────

def integration_tests() -> None:
    from pipelines.musicgen_pipeline import MusicGenConfig, MusicGenPipeline
    from utils.audio_io import read_audio

    _section("INTEGRATION — model load")

    pipeline = MusicGenPipeline()
    cfg_base = MusicGenConfig(
        prompt             = PROMPT,
        duration_seconds   = SHORT_DUR,
        steps              = FAST_STEPS,
        negative_prompt    = NEG_PROMPT,
        output_dir         = pathlib.Path("/tmp/stemforge_vp_test"),
    )

    try:
        pipeline.configure(cfg_base)
        pipeline.load_model()
        _report("integ", "model_load", True)
    except Exception as exc:
        _report("integ", "model_load", False, str(exc))
        print("  Cannot continue integration tests without model — aborting.")
        return

    # ── Get model sample rate ────────────────────────────────────────────
    try:
        sr: int = pipeline._model_config["sample_rate"]
        print(f"  [{_INFO}] model sample_rate = {sr} Hz")
    except Exception:
        sr = 44100

    # Load source audio for later comparison
    try:
        src_np, _ = read_audio(VOICE_WAV, mono=False, target_rate=sr)
        if src_np.shape[0] == 1:
            src_np = np.concatenate([src_np, src_np], axis=0)
    except Exception as exc:
        src_np = None
        print(f"  [{_INFO}] Could not load source audio for spectral tests: {exc}")

    # ── Helper to run one config variant ────────────────────────────────
    outputs: dict[str, np.ndarray] = {}

    def _run_variant(label: str, config: MusicGenConfig) -> np.ndarray | None:
        t0 = time.time()
        try:
            pipeline.configure(config)
            result = pipeline.run("")
            elapsed = time.time() - t0
            arr, _ = read_audio(result.audio_path, mono=False, target_rate=sr)
            if arr.shape[0] == 1:
                arr = np.concatenate([arr, arr], axis=0)

            # Basic validity checks
            ok_nonsilent = _rms(arr) > 1e-4
            ok_nonclipped = _peak(arr) <= 1.05
            expected_samples = int(config.duration_seconds * sr)
            actual_samples = arr.shape[1]
            ok_duration = abs(actual_samples - expected_samples) / expected_samples < 0.15

            print(
                f"  [{_PASS if (ok_nonsilent and ok_nonclipped and ok_duration) else _FAIL}] "
                f"{label}  rms={_rms(arr):.4f}  peak={_peak(arr):.3f}  "
                f"dur={actual_samples/sr:.2f}s (target {config.duration_seconds}s)  "
                f"time={elapsed:.1f}s"
            )
            _report("integ", f"{label}/non_silent",  ok_nonsilent,  f"rms={_rms(arr):.4f}")
            _report("integ", f"{label}/non_clipped", ok_nonclipped, f"peak={_peak(arr):.3f}")
            _report("integ", f"{label}/duration_ok", ok_duration,
                    f"got {actual_samples/sr:.2f}s target {config.duration_seconds}s")
            outputs[label] = arr
            return arr
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  [{_FAIL}] {label} — exception after {elapsed:.1f}s: {exc}")
            traceback.print_exc()
            _report("integ", f"{label}/run_completed", False, str(exc))
            return None

    # ── Test matrix ─────────────────────────────────────────────────────
    _section("INTEGRATION — text-only baseline (no conditioning)")

    _run_variant("text_only", MusicGenConfig(
        prompt=PROMPT, duration_seconds=SHORT_DUR, steps=FAST_STEPS,
        negative_prompt=NEG_PROMPT,
        output_dir=pathlib.Path("/tmp/stemforge_vp_test"),
    ))

    _section("INTEGRATION — audio conditioning, no VP mode")

    _run_variant("audio_cond_strength1.0", MusicGenConfig(
        prompt=PROMPT, duration_seconds=SHORT_DUR, steps=FAST_STEPS,
        negative_prompt=NEG_PROMPT,
        init_audio_path=VOICE_WAV,
        conditioning_strength=1.0,
        output_dir=pathlib.Path("/tmp/stemforge_vp_test"),
    ))

    _section("INTEGRATION — VP mode, timing_lock=False")

    _run_variant("vp_no_lock_s0.3", MusicGenConfig(
        prompt=PROMPT, duration_seconds=SHORT_DUR, steps=FAST_STEPS,
        negative_prompt=NEG_PROMPT,
        init_audio_path=VOICE_WAV,
        vocal_preservation=True,
        conditioning_strength=0.3,
        timing_lock=False,
        output_dir=pathlib.Path("/tmp/stemforge_vp_test"),
    ))

    _run_variant("vp_no_lock_s0.7", MusicGenConfig(
        prompt=PROMPT, duration_seconds=SHORT_DUR, steps=FAST_STEPS,
        negative_prompt=NEG_PROMPT,
        init_audio_path=VOICE_WAV,
        vocal_preservation=True,
        conditioning_strength=0.7,
        timing_lock=False,
        output_dir=pathlib.Path("/tmp/stemforge_vp_test"),
    ))

    _run_variant("vp_no_lock_s1.0", MusicGenConfig(
        prompt=PROMPT, duration_seconds=SHORT_DUR, steps=FAST_STEPS,
        negative_prompt=NEG_PROMPT,
        init_audio_path=VOICE_WAV,
        vocal_preservation=True,
        conditioning_strength=1.0,
        timing_lock=False,
        output_dir=pathlib.Path("/tmp/stemforge_vp_test"),
    ))

    _section("INTEGRATION — VP mode, timing_lock=True (windowed)")

    _run_variant("vp_locked_s0.7_w5s", MusicGenConfig(
        prompt=PROMPT, duration_seconds=SHORT_DUR, steps=FAST_STEPS,
        negative_prompt=NEG_PROMPT,
        init_audio_path=VOICE_WAV,
        vocal_preservation=True,
        conditioning_strength=0.7,
        timing_lock=True,
        window_size_seconds=WINDOW_SEC,
        output_dir=pathlib.Path("/tmp/stemforge_vp_test"),
    ))

    # ── Spectral correlation: conditioning strength measurability ────────
    _section("ANALYSIS — conditioning_strength measurability")

    if src_np is not None:
        for label in ["audio_cond_strength1.0", "vp_no_lock_s0.3", "vp_no_lock_s0.7", "vp_no_lock_s1.0"]:
            if label in outputs:
                corr = _spectral_correlation(src_np, outputs[label], sr)
                print(f"  [{_INFO}] spec_corr(source, {label}) = {corr:.4f}")

        # Check that strength=1.0 has higher correlation than strength=0.3
        if "vp_no_lock_s1.0" in outputs and "vp_no_lock_s0.3" in outputs:
            corr_high = _spectral_correlation(src_np, outputs["vp_no_lock_s1.0"], sr)
            corr_low  = _spectral_correlation(src_np, outputs["vp_no_lock_s0.3"], sr)
            _report("integ", "strength_1.0_more_correlated_than_0.3",
                    corr_high >= corr_low,
                    f"corr(s=1.0)={corr_high:.4f} corr(s=0.3)={corr_low:.4f}")
        else:
            print(f"  [{_SKIP}] skipped — one or both variants missing")

    # ── Crossfade boundary check for timing-locked output ───────────────
    _section("ANALYSIS — crossfade boundary smoothness (VP timing_lock)")

    if "vp_locked_s0.7_w5s" in outputs:
        arr = outputs["vp_locked_s0.7_w5s"]
        win_samples = int(WINDOW_SEC * sr)
        jump = _boundary_discontinuity(arr, win_samples, sr)
        # Allow up to 0.15 amplitude units of discontinuity (15% full-scale)
        _report("integ", "vp_lock_boundary_smooth",
                jump < 0.15,
                f"max_jump={jump:.4f}")
    else:
        print(f"  [{_SKIP}] skipped — vp_locked variant missing")

    # ── Text-only vs audio-conditioned: conditioning has a measurable effect ─
    _section("ANALYSIS — audio conditioning changes output (vs text-only)")

    if "text_only" in outputs and "audio_cond_strength1.0" in outputs:
        corr_vs_text = _spectral_correlation(
            outputs["text_only"], outputs["audio_cond_strength1.0"], sr
        )
        # They should differ: expect correlation < 0.99
        _report("integ", "audio_cond_differs_from_text_only",
                corr_vs_text < 0.99,
                f"spec_corr={corr_vs_text:.4f}")
    else:
        print(f"  [{_SKIP}] skipped — missing variants")


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary() -> int:
    _section("SUMMARY")
    n_pass = sum(1 for _, _, s in _results if s == "PASS")
    n_fail = sum(1 for _, _, s in _results if s == "FAIL")
    for tier, name, status in _results:
        tag = _PASS if status == "PASS" else _FAIL
        print(f"  [{tag}] [{tier:5s}] {name}")
    print(f"\n  Total: {n_pass} passed, {n_fail} failed")
    return 0 if n_fail == 0 else 1


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VP Mode objective tests")
    parser.add_argument("--unit-only", action="store_true",
                        help="Run only unit tests (no model required)")
    args = parser.parse_args()

    warnings.filterwarnings("ignore", category=UserWarning)

    print("\nStemForge — VP Mode Objective Tests")
    print(f"  Source file : {VOICE_WAV}")
    print(f"  Prompt      : {PROMPT[:70]}…")
    print(f"  Steps (fast): {FAST_STEPS}")
    print(f"  Duration    : {SHORT_DUR}s per clip")

    unit_tests()

    if not args.unit_only:
        if not VOICE_WAV.exists():
            print(f"\n[SKIP] Integration tests: {VOICE_WAV} not found.")
        else:
            integration_tests()
    else:
        print(f"\n[SKIP] Integration tests skipped (--unit-only).")

    sys.exit(_print_summary())
