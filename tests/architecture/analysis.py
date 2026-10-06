"""Static import analysis used by the architecture tests.

Pure ``ast`` - nothing is imported or executed. The rules below are the
executable version of the "Dependency rules" section of ARCHITECTURE.md.
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = "media_house"
STDLIB = sys.stdlib_module_names

TOP_LEVEL_PACKAGES = frozenset({"shared", "core", "modules", "presentation", "bootstrap"})
#: The shared kernel stays small on purpose: adding a package here is a conscious decision.
SHARED_PACKAGES = frozenset(
    {"errors", "logging", "configuration", "filesystem", "concurrency", "events", "types"},
)
CORE_PACKAGES = frozenset({"domain", "application", "infrastructure", "modules"})
FEATURE_LAYERS = frozenset({"domain", "application", "infrastructure", "presentation"})

DOMAIN_SHARED_OK = frozenset({"errors", "events", "types"})
THIRD_PARTY_OK = {"shared": {"pydantic", "platformdirs"}, "domain": set(), "application": set()}
QT = frozenset({"PySide6", "shiboken6", "PyQt5", "PyQt6"})
FORBIDDEN_STDLIB = {
    "domain": frozenset(
        {
            "subprocess",
            "sqlite3",
            "logging",
            "os",
            "shutil",
            "socket",
            "http",
            "urllib",
            "tempfile",
            "asyncio",
            "threading",
            "multiprocessing",
        },
    ),
    "application": frozenset({"subprocess", "sqlite3", "os", "shutil", "socket", "http", "urllib"}),
}
SUBPROCESS_ALLOWED_IN = "media_house.core.infrastructure.process.subprocess_runner"

#: What each layer may import *from the same feature or from core*.
LAYER_MAY_IMPORT = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "infrastructure": {"domain", "application", "infrastructure"},
    "presentation": {"application", "presentation"},
    "module": {"domain", "application", "infrastructure", "presentation", "module", "core_modules"},
    "core_modules": {"core_modules"},
}


@dataclass(frozen=True)
class Location:
    layer: str  # domain|application|infrastructure|presentation|module|core_modules|shared|bootstrap|root|unknown
    feature: str | None = None  # "core", a module name, or None (shared/shell/bootstrap)
    sub: str | None = None  # shared sub-package


@dataclass(frozen=True)
class Import:
    target: str
    lineno: int
    type_checking: bool
    lazy: bool  # inside a function body
    relative: bool = False


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    location: Location
    imports: tuple[Import, ...]


def classify(name: str) -> Location:
    parts = name.split(".")
    if parts[0] != ROOT:
        return Location("external")
    rest = parts[1:]
    if not rest or rest[0] in {"__main__"}:
        return Location("root")
    head = rest[0]
    if head == "shared":
        sub = rest[1] if len(rest) > 1 else None
        return Location("shared", None, sub)
    if head == "bootstrap":
        return Location("bootstrap")
    if head == "presentation":
        return Location("presentation", None)
    if head == "core":
        sub = rest[1] if len(rest) > 1 else None
        if sub == "domain":
            return Location("domain", "core")
        if sub == "application":
            return Location("application", "core")
        if sub == "infrastructure":
            return Location("infrastructure", "core")
        if sub == "modules":
            return Location("core_modules", "core")
        return Location("root" if sub is None else "unknown", "core")
    if head == "modules":
        if len(rest) == 1:
            return Location("root")
        feature = rest[1]
        if len(rest) == 2:
            return Location("root", feature)
        layer = rest[2]
        if layer == "module":
            return Location("module", feature)
        if layer in FEATURE_LAYERS:
            return Location(layer, feature)
        return Location("unknown", feature)
    return Location("unknown")


# ------------------------------------------------------------------ scanning
def scan(src_root: Path) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for path in sorted((src_root / ROOT).rglob("*.py")):
        relative = path.relative_to(src_root).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: list[Import] = []
        _collect(tree.body, imports, type_checking=False, lazy=False)
        modules[name] = ModuleInfo(name, classify(name), tuple(imports))
    return modules


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _collect(nodes: list[ast.stmt], out: list[Import], *, type_checking: bool, lazy: bool) -> None:
    for node in nodes:
        if isinstance(node, ast.Import):
            out.extend(Import(a.name, node.lineno, type_checking, lazy) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.append(
                    Import(
                        "." * node.level + (node.module or ""),
                        node.lineno,
                        type_checking,
                        lazy,
                        True,
                    )
                )
            elif node.module:
                out.append(Import(node.module, node.lineno, type_checking, lazy))
                out.extend(
                    Import(f"{node.module}.{a.name}", node.lineno, type_checking, lazy)
                    for a in node.names
                    if a.name != "*"
                )
        elif isinstance(node, ast.If):
            tc = type_checking or _is_type_checking(node.test)
            _collect(node.body, out, type_checking=tc, lazy=lazy)
            _collect(node.orelse, out, type_checking=type_checking, lazy=lazy)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _collect(node.body, out, type_checking=type_checking, lazy=True)
        elif isinstance(node, ast.ClassDef | ast.With | ast.For | ast.While):
            _collect(node.body, out, type_checking=type_checking, lazy=lazy)
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody, *[h.body for h in node.handlers]):
                _collect(block, out, type_checking=type_checking, lazy=lazy)


# ------------------------------------------------------------------ rules
def check_all(modules: dict[str, ModuleInfo]) -> list[str]:
    violations: list[str] = []
    known = set(modules)
    for info in modules.values():
        loc = info.location
        if loc.layer == "unknown":
            violations.append(
                f"{info.name}: not in a recognised package (shared/core/modules/presentation/bootstrap)"
            )
        if loc.layer == "shared" and loc.sub is not None and loc.sub not in SHARED_PACKAGES:
            violations.append(
                f"{info.name}: new shared-kernel package '{loc.sub}' needs an explicit decision"
            )
        for imp in info.imports:
            where = f"{info.name}:{imp.lineno}"
            if imp.relative:
                violations.append(f"{where}: relative import '{imp.target}' (use absolute imports)")
                continue
            top = imp.target.split(".")[0]
            if top == ROOT:
                if imp.target not in known and not _is_member_import(imp.target, known):
                    continue
                message = _check_internal(info, imp.target)
            else:
                message = _check_external(info, top, imp.target)
            if message:
                violations.append(f"{where}: imports {imp.target} - {message}")
    return violations


def _is_member_import(target: str, known: set[str]) -> bool:
    return target.rsplit(".", 1)[0] in known


def _check_external(info: ModuleInfo, top: str, target: str) -> str | None:
    layer = info.location.layer
    if top == "subprocess" and info.name != SUBPROCESS_ALLOWED_IN:
        return "subprocess may only be imported by the SubprocessRunner adapter"
    if top in QT and layer in {"shared", "domain", "application", "infrastructure", "core_modules"}:
        return f"Qt is only allowed in presentation, module.py and bootstrap (not in {layer})"
    if top in FORBIDDEN_STDLIB.get(layer, frozenset()):
        return f"'{top}' is not allowed in the {layer} layer"
    allowed = THIRD_PARTY_OK.get(layer)
    if allowed is not None and top not in STDLIB and top not in allowed:
        return f"third-party package '{top}' is not allowed in the {layer} layer"
    return None


def _check_internal(info: ModuleInfo, target: str) -> str | None:
    src, tgt = info.location, classify(target)
    if tgt.layer in {"root", "external"}:
        return None
    entry_point = info.name == f"{ROOT}.__main__"
    if src.layer == "bootstrap" or entry_point:
        return None
    if tgt.layer == "bootstrap":
        return "only bootstrap may import bootstrap"
    if tgt.layer == "core_modules" and src.layer not in {"module", "core_modules"}:
        return "the container/module contract is composition-only (bootstrap and module.py)"
    if src.layer == "shared":
        return (
            None if tgt.layer == "shared" else "the shared kernel must not depend on anything else"
        )
    if tgt.layer == "shared":
        if src.layer == "domain" and tgt.sub is not None and tgt.sub not in DOMAIN_SHARED_OK:
            return "domain may only use shared.errors / shared.events / shared.types"
        return None
    if src.feature == "core" and tgt.feature not in (None, "core"):
        return "core must not depend on feature modules"
    if tgt.feature not in (None, "core") and tgt.feature != src.feature:
        if src.feature is None:
            return "the shell must not know feature modules"
        if not target.startswith(f"{ROOT}.modules.{tgt.feature}.application.contracts"):
            return "cross-module imports must go through application.contracts"
        return None
    if tgt.layer not in LAYER_MAY_IMPORT.get(src.layer, set()):
        return f"{src.layer} must not depend on {tgt.layer}"
    return None


# ------------------------------------------------------------------ cycles
def find_cycles(modules: dict[str, ModuleInfo]) -> list[list[str]]:
    """Strongly connected components with more than one module (runtime imports only)."""
    graph: dict[str, set[str]] = {name: set() for name in modules}
    for info in modules.values():
        for imp in info.imports:
            if imp.type_checking or imp.lazy or imp.relative:
                continue
            resolved = _resolve(imp.target, modules)
            if resolved and resolved != info.name:
                graph[info.name].add(resolved)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in graph[node]:
            if neighbour not in index:
                visit(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))
    for node in graph:
        if node not in index:
            visit(node)
    return components


def _resolve(target: str, modules: dict[str, ModuleInfo]) -> str | None:
    parts = target.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in modules:
            return candidate
        parts.pop()
    return None
