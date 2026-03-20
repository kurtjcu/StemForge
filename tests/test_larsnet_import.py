"""Test that `from larsnet import LarsNet` works from non-project CWDs.

SEP-01 requirement: vendor/larsnet must be importable regardless of Python
process working directory. This test changes CWD to /tmp (which has no
larsnet package) and verifies the import still succeeds after the
sys.path insertion that LarsNetBackend uses.
"""
from __future__ import annotations

import os
import pathlib
import sys


def test_larsnet_import_from_tmp():
    """LarsNet is importable after sys.path insertion, even when CWD is /tmp."""
    # Resolve vendor/larsnet absolute path before changing CWD
    vendor_dir = str(
        pathlib.Path(__file__).resolve().parent.parent / "vendor"
    )

    original_cwd = os.getcwd()
    try:
        os.chdir("/tmp")

        # Mirror what LarsNetBackend.load() does
        if vendor_dir not in sys.path:
            sys.path.insert(0, vendor_dir)

        from larsnet import LarsNet  # noqa: F401

        assert LarsNet is not None

    finally:
        os.chdir(original_cwd)
        # Clean up sys.path to avoid polluting other tests
        if vendor_dir in sys.path:
            sys.path.remove(vendor_dir)


def test_larsnet_class_is_nn_module():
    """LarsNet is a torch.nn.Module subclass."""
    import torch.nn as nn

    vendor_dir = str(
        pathlib.Path(__file__).resolve().parent.parent / "vendor"
    )
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    from larsnet import LarsNet
    assert issubclass(LarsNet, nn.Module)
