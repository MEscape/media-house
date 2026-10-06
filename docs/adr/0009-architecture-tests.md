# ADR-0009: Custom AST architecture tests instead of import-linter

Status: accepted

**Context.** The dependency rules are the architecture; they must be checked automatically.

**Decision.** `tests/architecture/analysis.py` parses sources with `ast` and checks layer rules, forbidden
third-party/stdlib imports per layer, cross-module access, container usage, `subprocess` confinement, relative
imports, unrecognised packages and cycles (Tarjan SCC). The rules themselves are tested with synthetic source trees
so we know they catch violations (`test_rules_catch_violations.py`). Runs in <1 s as a normal pytest.

**Alternatives.** `import-linter` (excellent, declarative contracts; adds a dependency and cannot express
some of our "third-party per layer" rules without plugins). Switch if the custom analyzer grows unwieldy.

**Consequences.** + No dependency, rules expressed in Python, tested. - We maintain ~250 lines of analyzer.
Dynamic imports (`importlib`) are invisible to it; do not use them to bypass boundaries.
