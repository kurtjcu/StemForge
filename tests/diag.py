#!/usr/bin/env python3
import importlib
import traceback
import sys
from pathlib import Path

TESTS = [
    "test_torch",
    "test_demucs",
    "test_basicpitch",
    "test_audiocraft",
    "test_av",
    "test_librosa",
    "test_transformers",
    "test_spacy",
]

def run_test(module_name):
    print(f"\n=== Running {module_name} ===")
    try:
        mod = importlib.import_module(module_name)

        # If the test file defines a main() function, call it.
        if hasattr(mod, "main"):
            mod.main()

        print(f"[OK] {module_name}")
        return True

    except Exception as e:
        print(f"[FAIL] {module_name}")
        print("Error:", e)
        traceback.print_exc(limit=3)
        return False


def main():
    # Ensure we're running inside the tests directory
    test_dir = Path(__file__).parent
    sys.path.insert(0, str(test_dir))

    print("StemForge Diagnostics\n----------------------")

    results = {}
    for test in TESTS:
        results[test] = run_test(test)

    print("\n=== Summary ===")
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"{name:20} {status}")

    failed = [k for k, v in results.items() if not v]
    if failed:
        print("\nSome tests failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
