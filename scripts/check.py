"""Run every quality gate in order. This is the one command CI and developers use.

uv run python scripts/check.py          # all gates
uv run python scripts/check.py --fix    # auto-format and auto-fix lint first
"""

import argparse
import subprocess
import sys
import time

GATES: list[tuple[str, list[str]]] = [
    ("format", ["ruff", "format", "--check", "."]),
    ("lint", ["ruff", "check", "."]),
    ("types", ["mypy"]),
    ("tests (unit + architecture)", ["pytest", "tests/unit", "tests/architecture", "-q"]),
    ("tests (integration)", ["pytest", "tests/integration", "-q"]),
    ("tests (presentation)", ["pytest", "tests/presentation", "-q"]),
]


def run(name: str, command: list[str]) -> bool:
    started = time.monotonic()
    print(f"\n==> {name}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, check=False)
    ok = completed.returncode == 0
    print(f"<== {name}: {'ok' if ok else 'FAILED'} ({time.monotonic() - started:.1f}s)", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="format and auto-fix before checking")
    args = parser.parse_args()
    if args.fix:
        run("auto-format", ["ruff", "format", "."])
        run("auto-fix", ["ruff", "check", "--fix", "."])
    failed = [name for name, command in GATES if not run(name, command)]
    if failed:
        print(f"\nFAILED gates: {', '.join(failed)}")
        return 1
    print("\nAll quality gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
