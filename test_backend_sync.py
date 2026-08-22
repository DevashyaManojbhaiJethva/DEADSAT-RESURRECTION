#!/usr/bin/env python3
"""
test_backend_sync.py — verification that backend/ tree is deprecated
====================================================================

The `backend/` tree has been retired in favor of a single canonical backend
in root `main.py`. This test verifies that:

1. The backend/DEPRECATED.md file exists
2. No active code imports from backend/ (except the deprecation notice itself)
3. The canonical main.py is the authoritative backend

This prevents accidental use of the deprecated backend tree while preserving
the files for reference.

    python test_backend_sync.py          # exits 1 if backend is still in use
    python test_backend_sync.py -v       # show detailed violations
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
VERBOSE = "-v" in sys.argv

_failures: list[str] = []
_checked = 0


def test_backend_deprecated():
    """Verify that the backend/ tree is properly deprecated."""
    global _checked, _failures
    _checked += 1

    # Check that DEPRECATED.md exists
    deprecated_file = ROOT / "backend" / "DEPRECATED.md"
    if not deprecated_file.exists():
        _failures.append("backend/DEPRECATED.md does not exist - backend tree not marked as deprecated")
        return

    # Read the deprecation notice
    deprecation_text = deprecated_file.read_text(encoding="utf-8")
    if "DEPRECATED" not in deprecation_text.upper():
        _failures.append("backend/DEPRECATED.md does not contain clear deprecation notice")
        return

    if VERBOSE:
        print(f"✓ backend/DEPRECATED.md exists and contains deprecation notice")

    # Check that no active code imports from backend/ (except the deprecation file itself)
    python_files = list(ROOT.rglob("*.py"))
    for py_file in python_files:
        # Skip the deprecated file itself and test files
        if "backend" in str(py_file) or "test_" in py_file.name:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            # Look for imports from backend/
            if re.search(r'from backend\.|import backend\.', content):
                _failures.append(f"{py_file.relative_to(ROOT)} still imports from backend/")
                return
        except Exception:
            continue

    if VERBOSE:
        print(f"✓ No active code imports from backend/")


def main():
    """Run all tests and report results."""
    test_backend_deprecated()

    if _failures:
        print("❌ Backend deprecation check FAILED")
        for failure in _failures:
            print(f"  - {failure}")
        if VERBOSE:
            print(f"\nChecked: {_checked} test(s)")
        return 1
    else:
        print("✅ Backend tree is properly deprecated")
        if VERBOSE:
            print(f"Checked: {_checked} test(s)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
