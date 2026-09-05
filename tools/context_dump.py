"""Context-dump: de codebase samenvatten om in een Grok-chat te plakken.

    python -m tools.context_dump          # compacte samenvatting
    python -m tools.context_dump --full   # inclusief broncode van de kern
    python -m tools.context_dump --module bot   # alleen één onderdeel

Handig omdat SuperGrok geen repo-toegang heeft: dit geeft Grok in één plak
genoeg context om mee te denken, zonder de hele repo te kopiëren.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["radar", "bot", "backtest", "web"]
CORE_FILES = [
    "radar/run_radar.py", "radar/signals.py", "radar/grok.py",
    "bot/portfolio.py", "bot/config.py", "bot/scheduler.py",
]


def _py_files(package: str) -> List[Path]:
    return sorted(p for p in (ROOT / package).rglob("*.py")
                  if "__pycache__" not in p.parts)


def _signatures(path: Path) -> List[str]:
    """Publieke functies/klassen met hun eerste docstring-regel."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            args = ", ".join(a.arg for a in node.args.args)
            doc = (ast.get_docstring(node) or "").split("\n")[0]
            out.append(f"  def {node.name}({args})" + (f"  — {doc}" if doc else ""))
        elif isinstance(node, ast.ClassDef):
            doc = (ast.get_docstring(node) or "").split("\n")[0]
            out.append(f"  class {node.name}" + (f"  — {doc}" if doc else ""))
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and not sub.name.startswith("_"):
                    args = ", ".join(a.arg for a in sub.args.args)
                    out.append(f"    def {sub.name}({args})")
    return out


def _constants(path: Path) -> List[str]:
    """Config-constanten (HOOFDLETTERS) met hun waarde."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id.isupper():
                try:
                    val = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError):
                    continue
                out.append(f"  {t.id} = {val!r}")
    return out


def summary(modules: Optional[List[str]] = None) -> str:
    mods = modules or PACKAGES
    lines = [
        "=" * 70,
        "CRYPTODOKTER — CONTEXT-DUMP VOOR GROK",
        "=" * 70,
        "",
        "Repo: https://github.com/rickvdwiel/CRYPTODOKTER (branch main)",
        "Lees docs/HANDOFF-GROK.md voor de volledige handoff.",
        "",
        "Kort: radar die vroege crypto-trends opspoort (X/Grok + Bitvavo +",
        "DexScreener), scoort met risico-labels, virtueel verhandelt in een",
        "papieren portefeuille, backtest en toont op een dashboard.",
        "Regels: papier-only, fail-open, altijd eerlijk over rug-risico.",
        "",
    ]
    for pkg in mods:
        if not (ROOT / pkg).is_dir():
            continue
        lines.append("-" * 70)
        lines.append(f"PAKKET: {pkg}/")
        lines.append("-" * 70)
        for f in _py_files(pkg):
            rel = f.relative_to(ROOT)
            if f.name == "__init__.py":
                continue
            head = (f.read_text(encoding="utf-8").split('"""')[1].split("\n")[0].strip()
                    if '"""' in f.read_text(encoding="utf-8") else "")
            lines.append(f"\n{rel}  ({sum(1 for _ in f.open())} regels)"
                         + (f"\n  # {head}" if head else ""))
            consts = _constants(f)
            if consts:
                lines.append("  -- constanten --")
                lines.extend(consts)
            sigs = _signatures(f)
            if sigs:
                lines.extend(sigs)
        lines.append("")
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    if tests:
        lines.append("-" * 70)
        lines.append(f"TESTS: {', '.join(t.name for t in tests)}")
        lines.append("Draaien: python -m unittest discover -s tests -t . -q")
        lines.append("-" * 70)
    return "\n".join(lines)


def full_source(files: Optional[List[str]] = None) -> str:
    out = ["", "=" * 70, "BRONCODE VAN DE KERN", "=" * 70]
    for rel in (files or CORE_FILES):
        p = ROOT / rel
        if not p.exists():
            continue
        out.append(f"\n### {rel}\n```python\n{p.read_text(encoding='utf-8')}\n```")
    return "\n".join(out)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Context-dump voor Grok")
    ap.add_argument("--full", action="store_true", help="inclusief broncode van de kern")
    ap.add_argument("--module", action="append", help="alleen dit pakket (herhaalbaar)")
    args = ap.parse_args(argv)

    print(summary(args.module))
    if args.full:
        print(full_source())
    print("\nPlak dit in Grok met: 'Dit is de CryptoDokter-codebase, help me met ...'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
