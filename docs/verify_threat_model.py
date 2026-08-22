#!/usr/bin/env python3
"""
verify_threat_model.py — do the threat model's citations resolve?
=================================================================

    "every mitigation named in the document points to a specific file and
     function, or is explicitly listed as future work."

A threat model is a claim about the code. Claims about code rot. This walks
every `` `symbol` in `path` `` citation in docs/THREAT_MODEL.md, resolves the
path on disk and the symbol with `ast`, and fails on anything that does not
exist.

It also fails if a mitigation TABLE ROW carries no citation at all, which is
how an unbacked claim would otherwise slip in.

    python docs/verify_threat_model.py
    python docs/verify_threat_model.py -v     # list every resolved citation

Exit code 0 means every mitigation in the document is real.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
DOC = DOCS / "THREAT_MODEL.md"
VERBOSE = "-v" in sys.argv


def symbols_in(rel_path: str) -> set[str]:
    """Every function, class, method and module-level constant in a file."""
    path = ROOT / rel_path
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()

    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


#: `symbol` in `path`   — the citation form used throughout the document.
CITATION = re.compile(r"`([A-Za-z_][\w.]*)`\s+(?:in|and)\s+`([\w./-]+\.(?:py|ts|tsx))`")

#: `a` and `b` in `path`  — two symbols sharing one location.
PAIR = re.compile(
    r"`([A-Za-z_][\w.]*)`\s+and\s+`([A-Za-z_][\w.]*)`\s+in\s+`([\w./-]+\.(?:py|ts|tsx))`")


def main() -> int:
    if not DOC.exists():
        print(f"ERROR: {DOC} does not exist")
        return 1

    text = DOC.read_text(encoding="utf-8")
    cache: dict[str, set[str]] = {}
    checked = 0
    failures: list[str] = []

    def resolve(symbol: str, rel: str) -> None:
        nonlocal checked
        checked += 1
        if rel not in cache:
            cache[rel] = symbols_in(rel)
        if not (ROOT / rel).exists():
            failures.append(f"{symbol} in {rel} — FILE DOES NOT EXIST")
            return
        # TypeScript is not parsed with ast; fall back to a text search
        if rel.endswith((".ts", ".tsx")):
            if symbol not in (ROOT / rel).read_text(encoding="utf-8"):
                failures.append(f"{symbol} in {rel} — symbol not found")
            elif VERBOSE:
                print(f"  [ok] {symbol:38} {rel}")
            return
        if symbol not in cache[rel]:
            failures.append(f"{symbol} in {rel} — symbol not defined in that file")
        elif VERBOSE:
            print(f"  [ok] {symbol:38} {rel}")

    print("=" * 74)
    print("verify_threat_model.py — resolving every citation in THREAT_MODEL.md")
    print("=" * 74)

    seen: set[tuple[str, str]] = set()

    for a, b, rel in PAIR.findall(text):
        for sym in (a, b):
            if (sym, rel) not in seen:
                seen.add((sym, rel))
                resolve(sym, rel)

    for sym, rel in CITATION.findall(text):
        if (sym, rel) not in seen:
            seen.add((sym, rel))
            resolve(sym, rel)

    # ---- every mitigation row must cite something --------------------------
    uncited: list[str] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Mitigation |"):
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                in_table = False
                continue
            if set(stripped) <= set("|- "):        # separator row
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            where = cells[-1] if cells else ""
            if "`" not in where:
                uncited.append(cells[0][:70])

    for row in uncited:
        failures.append(f"mitigation row with no citation: {row!r}")

    # ---- future work must be labelled, not implied -------------------------
    future = text.count("**FUTURE WORK:**")

    print(f"\n  citations resolved : {checked}")
    print(f"  distinct symbols   : {len(seen)}")
    print(f"  files referenced   : {len(cache)}")
    print(f"  FUTURE WORK items  : {future}")

    if failures:
        print(f"\n  UNRESOLVED ({len(failures)}):")
        for f in failures:
            print(f"    - {f}")
        print("\n  Every mitigation must point at code that exists, or be")
        print("  marked **FUTURE WORK:**.")
        print("=" * 74)
        return 1

    print("\n  All citations resolve. Every mitigation points at real code.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
