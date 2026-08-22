#!/usr/bin/env python3
"""
check_lockfile_sync.py — does package-lock.json still match package.json?
=========================================================================

`npm ci` refuses to run on ANY mismatch between the two, and its error
(EUSAGE, several screens of npm internals) does not say which packages are
out of step. This runs offline, in a second, and names them.

Why this exists: Prompts 0.1 and 5.1 edited `frontend/package.json` — adding
`@types/react` / `@types/react-dom`, removing `@google/genai`, `express` and
`dotenv` — in an environment where the npm registry was unreachable, so the
lockfile could not be regenerated. CI would have failed on `npm ci` with a
message that pointed at nothing useful.

    python check_lockfile_sync.py           # exit 1 on drift
    python check_lockfile_sync.py --quiet   # exit code only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "frontend" / "package.json"
LOCK = ROOT / "frontend" / "package-lock.json"
QUIET = "--quiet" in sys.argv


REQS = ROOT / "requirements.txt"
PY_LOCK = ROOT / "requirements.lock"


def _requirement_names(path: Path) -> dict[str, str]:
    """Parse `name` -> `raw spec` from a requirements file, ignoring comments."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("[")[0]
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            if sep in name:
                name = name.split(sep)[0]
                break
        out[name.strip().lower().replace("_", "-")] = line
    return out


def check_python_lock() -> int:
    """
    requirements.lock vs requirements.txt.

    ABSENT is a warning, not a failure: the lock can only be generated on a
    machine that can reach PyPI, and this repository has been developed
    without that. STALE is a failure — a lock that no longer covers what
    requirements.txt declares is worse than none, because it looks
    authoritative.
    """
    if not REQS.exists():
        print("  [FAIL] requirements.txt is missing")
        return 1

    declared = _requirement_names(REQS)

    if not PY_LOCK.exists():
        if not QUIET:
            print("  [WARN] requirements.lock does not exist — the Python "
                  "environment is not pinned")
            print("         Two training runs may resolve different versions of "
                  "torch/numpy and produce different weights.")
            print("         Generate it on a machine with PyPI access:")
            print("             python -m venv .venv && . .venv/bin/activate")
            print("             pip install -r requirements.txt")
            print("             pip freeze --exclude-editable > requirements.lock")
        return 0                      # warning only — see docstring

    locked = _requirement_names(PY_LOCK)
    missing = sorted(n for n in declared if n not in locked)
    unpinned = sorted(n for n, spec in locked.items() if "==" not in spec)

    if missing or unpinned:
        if not QUIET:
            if missing:
                print("  [FAIL] declared in requirements.txt but ABSENT from "
                      "requirements.lock:")
                for n in missing:
                    print(f"           + {declared[n]}")
            if unpinned:
                print("  [FAIL] requirements.lock entries that are not exact "
                      "(== ) pins:")
                for n in unpinned:
                    print(f"           ~ {locked[n]}")
            print("         Regenerate: pip freeze --exclude-editable > requirements.lock")
        return 1

    if not QUIET:
        print(f"  [PASS] requirements.lock pins {len(locked)} packages, "
              f"covers all {len(declared)} declared")
    return 0


def main() -> int:
    if not QUIET:
        print("=" * 74)
        print("check_lockfile_sync.py — dependency pinning")
        print("=" * 74)
        print("requirements.lock vs requirements.txt")
        print("-" * 74)
    py_rc = check_python_lock()
    if not QUIET:
        print()

    if not PKG.exists() or not LOCK.exists():
        print(f"ERROR: missing {'package.json' if not PKG.exists() else 'package-lock.json'}")
        return 1

    pkg = json.loads(PKG.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    declared = dict(pkg.get("dependencies", {}))
    declared.update(pkg.get("devDependencies", {}))

    root_entry = lock.get("packages", {}).get("", {})
    locked = dict(root_entry.get("dependencies", {}))
    locked.update(root_entry.get("devDependencies", {}))

    missing = sorted(set(declared) - set(locked))   # in package.json, not locked
    stale = sorted(set(locked) - set(declared))     # locked, no longer declared
    changed = sorted(name for name in set(declared) & set(locked)
                     if declared[name] != locked[name])

    ok = not (missing or stale or changed)

    if not QUIET:
        print("-" * 74)
        print("frontend/package-lock.json vs package.json")
        print("-" * 74)
        if ok:
            print(f"  [PASS] {len(declared)} dependencies, lockfile in sync")
        else:
            if missing:
                print("  [FAIL] declared in package.json but ABSENT from the lockfile:")
                for n in missing:
                    print(f"           + {n}@{declared[n]}")
            if stale:
                print("  [FAIL] in the lockfile but NO LONGER declared:")
                for n in stale:
                    print(f"           - {n}@{locked[n]}")
            if changed:
                print("  [FAIL] version ranges disagree:")
                for n in changed:
                    print(f"           ~ {n}: package.json {declared[n]} vs lock {locked[n]}")
            print()
            print("  `npm ci` will refuse to install in this state.")
            print("  Fix on a machine with registry access:")
            print("      cd frontend && npm install && git add package-lock.json")
            print("  (npm install rewrites the lock; npm ci never does — that is the point.)")
        print("=" * 74)

    return 0 if (ok and py_rc == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
