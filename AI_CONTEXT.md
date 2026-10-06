# Media-House — AI Context (paste this instead of the codebase)

You are adding code to **Media-House**, a Python 3.13 / PySide6 desktop app built as a **modular monolith** with
**hexagonal architecture** and pragmatic DDD. The architecture is enforced by automated tests: code that breaks the
rules below FAILS the build. Follow this document exactly; do not invent new layers, packages or patterns.

Your job for a task: **create one new feature module** (or extend an existing one) by copying the shape below.
Do NOT edit the main window, `shared/`, other modules, or `bootstrap/` (except one line in `bootstrap/modules.py`).

---------------------------------------------------------------------------------------------------

## 1. Stack and tooling

* Python >= 3.13 (use PEP 695 generics: `class Ok[T]`, `def f[T](...)`), `uv` for everything.
* UI: PySide6 (Widgets). DB: stdlib `sqlite3` behind repository ports. Validation of *external* data: pydantic.
* Strict typing (`mypy --strict`), ruff (format + lint + security), pytest + pytest-qt. Warnings are test errors.
* **Absolute imports only** (`from media_house.x.y import Z`). Never relative imports.
* Commands: `uv run python scripts/check.py` (all gates) · `uv run pytest` · `uv run ruff format .` · `uv run mypy`.

## 2. Package map (src/media_house/)

```text
bootstrap/     composition root (cli, application, composition, desktop, modules.py). Knows everything.
shared/        tiny kernel: errors, logging, configuration, filesystem, concurrency, events, types. NO business logic.
core/
  domain/            AggregateRoot
  application/ports/ Clock, ProcessRunner (+ProcessSpec, ProcessResult, OutputLine)
  infrastructure/    SystemClock, SubprocessRunner (ONLY file allowed to `import subprocess`)
  modules/           ApplicationModule protocol, Container (composition-only), install_modules
modules/<name>/    one feature module (vertical slice) — see §5
presentation/      the shell: MainWindow, extension registry, UiJobRunner, error boundary, theme, resources
```

## 3. HARD RULES (enforced by tests/architecture)

| Layer (inside a module or core) | May import | MUST NOT import |
| --- | --- | --- |
| `domain` | stdlib (not `os`, `logging`, `sqlite3`, `subprocess`, `threading`, `socket`, `http`, `urllib`, `shutil`, `tempfile`, `asyncio`), own domain, `core.domain`, `shared.errors`, `shared.events`, `shared.types` | Qt, application, infrastructure, presentation, other `shared.*` |
| `application` | own domain, `core.domain`, `core.application` (ports), `shared.*` | infrastructure, presentation, Qt, `sqlite3`, `subprocess`, `os`, `shutil`, `core.modules` (container) |
| `infrastructure` | own domain, own application, ports, `shared.*`, any non-Qt library | presentation, Qt |
| `presentation` | own application (use cases, DTOs, commands), `shared.*`, shell `media_house.presentation.*`, Qt | **domain**, **infrastructure**, `core.modules`, other modules |
| `module.py` | all four of its own layers, `core.modules`, `core.application.ports`, `media_house.presentation.extension|qt`, `shared.*`, other modules' `application.contracts` | other modules' internals |

Also:
1. **Cross-module imports are allowed ONLY for `media_house.modules.<other>.application.contracts`.** Never import another module's domain, use cases, infrastructure, presentation, or DB.
2. `shared/` imports only stdlib/pydantic/platformdirs; never add a package to it. If you think you need to: you don't — use `core` (a port) or the module's `contracts.py`.
3. `Container` is used ONLY in `bootstrap/` and `modules/<m>/module.py`. Never pass it into business classes. Constructor injection only.
4. `subprocess`, `os.environ`, `os.getenv`, `print()` are banned in library code. Run tools via the `ProcessRunner` port; read config via `AppSettings`; log via `get_logger`.
5. No global mutable state, no singletons, no `shell=True`, no `except Exception: pass`, no secrets in logs/messages.
6. Never block the UI thread: DB/file/process work from the UI goes through `UiJobRunner`.
7. Never call `QApplication.exec()` in tests (run in a subprocess if truly needed).

## 4. APIs you will use (exact imports)

```python
# --- errors (shared/errors) ---------------------------------------------------------------
from media_house.shared.errors import (
    MediaHouseError, DomainError, InvariantViolation, ValidationError, ApplicationError, NotFoundError,
    ConflictError, OperationCancelledError, InfrastructureError, PersistenceError, ExternalSystemError,
    ToolNotFoundError, ProcessTimeoutError, ProcessFailedError, ConfigurationError, UnexpectedError,
    Ok, Err, Result,
)
# All errors: Err(message, user_message="...", details={...}); has .code .category .error_id .user_message .details
# ValidationError(message, field="name", user_message=...). Subclass DomainError for module-specific rules:
#   class FooNameTaken(DomainError): code = "foo.name_taken"; def __init__(self, name): super().__init__(..., user_message=...)
from media_house.shared.errors.handler import ErrorHandler, ErrorReport   # (not re-exported from shared.errors)

# --- events ----------------------------------------------------------------------------------
from media_house.shared.events import DomainEvent, ApplicationEvent, EventPublisher, EventBus
#   @dataclass(frozen=True, kw_only=True, slots=True)
#   class FooCreated(DomainEvent): foo_id: str          # occurred_at is REQUIRED kw (passed in; domain has no clock)
#   publisher.publish(event)  -> sync, on the caller's thread, handler errors isolated. bus.subscribe(Type, handler)

# --- logging ----------------------------------------------------------------------------------
from media_house.shared.logging import get_logger, bind_context
log = get_logger(__name__); log.info("Imported", asset_id=a.id, count=3)   # keyword fields; sensitive keys auto-redacted

# --- paths / config -----------------------------------------------------------------------------
from media_house.shared.filesystem import AppPaths, resolve_within
#   paths.data_dir / config_dir / cache_dir / log_dir / temp_dir ; with paths.temporary_directory() as tmp: ...
#   resolve_within(root, untrusted_path) -> Path  (raises ValidationError on traversal)
from media_house.shared.configuration import AppSettings  # settings.concurrency.max_workers, settings.ui.theme, ...

# --- time / processes (ports from core) ----------------------------------------------------------
from media_house.core.application.ports import Clock, ProcessRunner, ProcessSpec, ProcessResult, OutputLine
#   clock.now() -> aware UTC datetime
#   runner.run(ProcessSpec("ffprobe", ["-v","error", str(path)], timeout_seconds=30, check=True),
#              cancellation=ctx.cancellation, on_output=lambda line: ...) -> ProcessResult(exit_code, stdout, stderr, duration_seconds)
#   raises ToolNotFoundError / ProcessTimeoutError / ProcessFailedError(check=True) / OperationCancelledError. No shell, argv list only.

# --- background jobs ---------------------------------------------------------------------------------
from media_house.shared.concurrency import JobContext, JobScheduler, JobProgress, JobState, CancellationToken
#   Work is a plain function: def execute(self, ctx: JobContext) -> T:
#       ctx.raise_if_cancelled(); ctx.progress.report(current, total, "message"); ctx.cancellation.wait(0.1)
#   In unit tests run it directly with JobContext.detached().

# --- domain base ---------------------------------------------------------------------------------------
from media_house.core.domain import AggregateRoot   # self._record(event) ; aggregate.pull_events() -> list[DomainEvent]
from media_house.shared.types import new_id         # opaque unique id string

# --- composition (module.py / bootstrap ONLY) ---------------------------------------------------------
from media_house.core.modules import Container, ApplicationModule
#   container.register_factory(Type, lambda c: Impl(c.resolve(Dep)))   # lazy singleton; key may be a Protocol
#   container.register_instance(Type, obj); container.add_to_collection(Type, factory); c.resolve(Type)
# Always available to resolve: AppSettings, AppPaths, ErrorHandler, EventBus, EventPublisher, JobScheduler,
#   ProcessRunner, Clock.  Available once the desktop UI is running (resolve lazily inside UI factories only):
#   UiJobRunner, StatusReporter.

# --- UI (presentation) -----------------------------------------------------------------------------------
from media_house.presentation.extension import (
    UiContributor, UiRegistry, ActionContribution, ViewContribution, DockContribution)
#   ActionContribution(id, text, menu, callback, shortcut=None, status_tip="")   # menu: "File"/"View"/"Help"/your own name
#   ViewContribution(id, title, factory: Callable[[], QWidget])                  # page in nav list, built lazily
#   DockContribution(id, title, factory, area=Qt.DockWidgetArea.BottomDockWidgetArea)
from media_house.presentation.qt.job_bridge import UiJobRunner
#   jobs.submit("Name", work(ctx)->T, on_success=cb(T), on_error=cb(ErrorReport), on_cancelled=cb(),
#               on_progress=cb(JobProgress), on_finished=cb())   -> JobHandle (handle.cancel())
#   Call on the UI thread only. All callbacks run on the UI thread. Unhandled failures show the global error dialog.
from media_house.presentation.qt.status import StatusReporter       # status.show("Saved", 5000)
```

## 5. Module blueprint (copy this shape; omit layers you do not need)

```text
modules/foo/
  __init__.py
  domain/            values.py  events.py  foo.py (aggregate)  repository.py (port + domain errors)
  application/       dto.py  commands.py  create_foo.py  list_foos.py  contracts.py  events.py(optional)
  infrastructure/    sqlite_repository.py
  presentation/      strings.py  models/foo_list_model.py  viewmodels/foo_viewmodel.py  views/foo_view.py  contributor.py
  module.py          registration (the module's own composition root)
```
Plus: one line in `bootstrap/modules.py`, tests in `tests/` (§8). Each module owns its own storage (e.g. `data_dir / "foo.db"`);
never read another module's database.

### 5.1 Domain (pure Python, no I/O, no clock, no logging)
```python
# domain/values.py — immutable, validate in __post_init__, expose a normalising factory
@dataclass(frozen=True, slots=True)
class FooId:
    value: str
    def __post_init__(self) -> None:
        if not self.value: raise InvariantViolation("FooId must not be empty")
    @classmethod
    def new(cls) -> "FooId": return cls(new_id())

@dataclass(frozen=True, slots=True)
class FooName:
    MAX_LENGTH: ClassVar[int] = 80
    value: str
    def __post_init__(self) -> None:
        if not self.value or self.value != self.value.strip():
            raise InvariantViolation("Name must be non-empty and trimmed", user_message="Please enter a name.")
    @classmethod
    def of(cls, raw: str) -> "FooName": return cls(raw.strip())
    @property
    def key(self) -> str: return self.value.casefold()          # uniqueness key

# domain/events.py
@dataclass(frozen=True, kw_only=True, slots=True)
class FooCreated(DomainEvent):
    foo_id: str

# domain/foo.py — aggregate: state changes via methods, events recorded, time passed in
class Foo(AggregateRoot):
    def __init__(self, *, id: FooId, name: FooName, created_at: datetime) -> None:  # noqa: A002
        super().__init__(); self.id = id; self.name = name; self.created_at = created_at
    @classmethod
    def create(cls, name: FooName, *, now: datetime) -> "Foo":
        foo = cls(id=FooId.new(), name=name, created_at=now)
        foo._record(FooCreated(occurred_at=now, foo_id=foo.id.value))
        return foo

# domain/repository.py — PORT (Protocol) + business errors
class FooNameTaken(DomainError):
    code = "foo.name_taken"
    def __init__(self, name: FooName) -> None:
        super().__init__(f"Name in use: {name}", user_message=f"'{name}' already exists.")

class FooRepository(Protocol):
    def add(self, foo: Foo) -> None: ...            # raises FooNameTaken
    def get(self, foo_id: FooId) -> Foo | None: ...
    def list_all(self) -> Sequence[Foo]: ...
    def name_exists(self, name: FooName) -> bool: ...
```

### 5.2 Application (use cases: one class per action, constructor-injected ports, explicit results)
```python
# application/dto.py          @dataclass(frozen=True, slots=True) class FooDto: id: str; name: str; created_at: datetime
# application/commands.py     @dataclass(frozen=True, slots=True) class CreateFooCommand: name: str

class CreateFoo:
    def __init__(self, repository: FooRepository, clock: Clock, events: EventPublisher) -> None: ...
    def execute(self, command: CreateFooCommand) -> Result[FooDto, ValidationError | ConflictError]:
        try:
            name = FooName.of(command.name)
        except DomainError as exc:                                   # expected failure -> Err, not raise
            return Err(ValidationError(str(exc), field="name", user_message=exc.user_message))
        if self._repository.name_exists(name):
            return Err(ConflictError("duplicate", user_message=f"'{name}' already exists."))
        foo = Foo.create(name, now=self._clock.now())
        try:
            self._repository.add(foo)
        except FooNameTaken:                                         # lost a race
            return Err(ConflictError("duplicate", user_message=f"'{name}' already exists."))
        for event in foo.pull_events():                              # publish AFTER persisting
            self._events.publish(event)
        return Ok(to_dto(foo))                                       # map domain -> DTO; never leak domain objects

# Long-running use case = plain method taking JobContext:
class ImportFoos:
    def execute(self, ctx: JobContext) -> ImportSummary:
        for i, item in enumerate(items, 1):
            ctx.raise_if_cancelled(); ...; ctx.progress.report(i, len(items), f"Imported {item}")
```
Rules: expected failures (bad input, duplicates, not found) → `Err(...)`; infrastructure failures → let typed exceptions propagate.
Use cases never import Qt, never touch paths/DB/processes directly (use ports).

### 5.3 Public contract for other modules (only if needed — keep tiny and stable)
```python
# application/contracts.py — the ONLY file other modules may import from this module
class FooCatalog(Protocol):
    def list_foos(self) -> Sequence[FooDto]: ...
# any event another module should react to must be re-exported/defined here too.
```

### 5.4 Infrastructure (adapter implements the port; wrap library errors; keep DTO/ORM rows out of the domain)
```python
class SqliteFooRepository:                       # structurally implements FooRepository
    def __init__(self, database_path: Path) -> None: ...
    def add(self, foo: Foo) -> None:
        try: ...  # one short-lived sqlite3 connection per operation (thread-safe); closing(sqlite3.connect(...))
        except sqlite3.IntegrityError as exc: raise FooNameTaken(foo.name) from exc
        except sqlite3.Error as exc: raise PersistenceError(f"Failed to add: {exc}", details={"database": str(self._path)}) from exc
    # map rows -> domain with explicit code; corrupt data -> PersistenceError from exc
```
Always `raise X(...) from exc`. Close connections (tests turn ResourceWarnings into failures).

### 5.5 module.py (registration — the only place ports meet adapters for this module)
```python
class FooModule:
    name: str = "foo"
    def register(self, container: Container) -> None:
        container.register_factory(FooRepository,
            lambda c: SqliteFooRepository(c.resolve(AppPaths).data_dir / "foo.db"))
        container.register_factory(CreateFoo,
            lambda c: CreateFoo(c.resolve(FooRepository), c.resolve(Clock), c.resolve(EventPublisher)))
        container.register_factory(ListFoos, lambda c: ListFoos(c.resolve(FooRepository)))
        container.register_factory(FooCatalog, lambda c: c.resolve(ListFoos))      # expose public contract
        container.add_to_collection(UiContributor,                                   # UI hook (lazy)
            lambda c: FooUiContributor(lambda: _build_viewmodel(c)))

def _build_viewmodel(c: Container) -> FooViewModel:
    return FooViewModel(c.resolve(CreateFoo), c.resolve(ListFoos), c.resolve(UiJobRunner), c.resolve(StatusReporter))
```
Then in `bootstrap/modules.py`: `from media_house.modules.foo.module import FooModule` and add `FooModule()` to the returned tuple.
Registration order does not matter (factories are lazy).

### 5.6 Presentation (passive widgets, logic in the view model, strings centralised)
```python
# viewmodels/foo_viewmodel.py — QObject + signals; talks to use cases via UiJobRunner; NO widgets
class FooViewModel(QObject):
    foos_changed = Signal(object)        # Sequence[FooDto]
    busy_changed = Signal(bool)
    form_error_changed = Signal(str)     # "" clears
    def __init__(self, create: CreateFoo, catalog: ListFoos, jobs: UiJobRunner, status: StatusReporter,
                 parent: QObject | None = None) -> None: ...
    def refresh(self) -> None:
        self._begin()
        self._jobs.submit("Load foos", lambda _ctx: self._catalog.list_foos(),
                          on_success=self.foos_changed.emit, on_finished=self._end)
    def create_foo(self, name: str) -> None:
        self._begin()
        self._jobs.submit("Create foo", lambda _ctx: self._create.execute(CreateFooCommand(name)),
                          on_success=self._on_created, on_finished=self._end)
    def _on_created(self, result: Result[FooDto, ValidationError | ConflictError]) -> None:
        match result:
            case Ok(dto): self._status.show(f"Created {dto.name}"); self.refresh()
            case Err(error): self.form_error_changed.emit(error.user_message)

# views/foo_view.py — QWidget: build widgets, connect signals both ways, set setAccessibleName(...) on inputs/lists,
#   no business logic, NO stylesheet strings (styling lives in presentation/resources/styles/base.qss).
# models/*.py — QAbstractListModel subclasses use @override on rowCount/data (from typing import override).
# strings.py — every user-facing string as a constant (future i18n).

# contributor.py
class FooUiContributor:
    def __init__(self, viewmodel_factory: Callable[[], FooViewModel]) -> None: ...   # cache the VM instance
    def contribute(self, registry: UiRegistry) -> None:
        registry.add_view(ViewContribution("foo.main", "Foos", self._create_view))     # ids must be unique app-wide
        registry.add_action(ActionContribution("foo.refresh", "Refresh Foos", "Foo", lambda: self._vm().refresh(), shortcut="F5"))
        # registry.add_dock(DockContribution("foo.panel", "Foo Panel", lambda: FooPanel(self._vm())))
```
Prefix every contribution id with the module name (`foo.…`). The main window needs no change.

## 6. Cross-module integration

* **Consume another module's capability:** depend on its `application.contracts` Protocol. In *your* use case take the
  Protocol in the constructor; in *your* `module.py` wire it: `c.resolve(FooCatalog)` (import it from
  `media_house.modules.foo.application.contracts`). Never import their use-case classes or repository.
* **React to something that happened elsewhere:** subscribe on `EventBus` to an event type exposed through the provider's
  `contracts.py`; handlers run on the publisher's thread — keep them short or submit a job. Do this in `module.py`.
* **Expose something yourself:** add a Protocol/DTO/event to your `application/contracts.py`, register it in your `module.py`.
* **Need a new shared capability** (e.g. file storage, HTTP client, GPU runner)? Define a Protocol in
  `core/application/ports/`, adapter in `core/infrastructure/`, register it in `bootstrap/composition.py`. Ask first.

## 7. Cross-cutting recipes

* **Errors:** expected → `Err`; unexpected → raise typed error `from exc`. UI never shows raw exceptions: the job bridge/
  global boundary turns them into a dialog with a reference id. Add module errors by subclassing `DomainError`/`ApplicationError`.
* **External tools (FFmpeg etc.):** only via `ProcessRunner` inside an infrastructure adapter or a use case that receives the port.
  Validate/resolve untrusted paths with `resolve_within`. Pass `cancellation=ctx.cancellation`; set `timeout_seconds`.
  Treat a missing tool as `ToolNotFoundError` (already user-friendly). Never build shell strings.
* **Temp files:** `with paths.temporary_directory() as tmp:` — never hard-code paths; never use raw `os.path`.
* **Settings:** read via `AppSettings` injected from `module.py`; do not read env vars. Secrets only via `settings.secrets[...]` (SecretStr).
* **Concurrency:** jobs are cooperative: call `ctx.raise_if_cancelled()` in loops; blocking calls that cannot be cancelled
  belong in a subprocess. Never touch widgets from non-UI threads.
* **Time:** inject `Clock`; domain receives `now` as an argument.
* **Logging:** `log = get_logger(__name__)`; fields as kwargs; never log secrets or full user media paths unnecessarily.
* **Style:** frozen dataclasses (`slots=True`) for DTOs/commands/value objects; Protocols for ports; small classes;
  explicit return types; no `Any` unless unavoidable; `raise ... from exc`; docstring on every public class stating its role.

## 8. Tests you must add (mirror the source layout)

| What | Where | Rules |
| --- | --- | --- |
| Value objects, aggregates | `tests/unit/<module>/test_domain.py` | no Qt/files/DB; use `T0` from fakes for time |
| Use cases | `tests/unit/<module>/test_use_cases.py` | in-memory fake repo + `FixedClock` + `RecordingPublisher`; cover Ok, each Err, cancellation (`JobContext`) |
| Repository adapter | `tests/integration/test_<module>_repository.py` | **contract tests parametrised over fake AND real adapter**; `tmp_path`; corrupt-data case |
| View model / view | `tests/presentation/test_<module>_ui.py` | `qtbot`; `ImmediateJobScheduler` for deterministic VM tests, `ThreadPoolJobScheduler` + `qtbot.waitUntil` for async |
| Architecture | nothing to add | `tests/architecture` automatically scans your module |

Fakes available in `tests/support/fakes.py`: `FixedClock`, `T0`, `RecordingPublisher`, `ImmediateJobScheduler`,
`InMemoryWorkspaceRepository` (copy its pattern: return re-hydrated copies, raise the domain error on duplicates).
Fixtures in `tests/presentation/conftest.py`: `dispatcher`, `runner`, `unhandled_errors`. Mark nothing manually except where
`pytestmark = pytest.mark.integration|presentation` is used in that folder.

## 9. Definition of done for a module

- [ ] Layers follow §3; `uv run pytest tests/architecture` passes without editing the rules.
- [ ] Only `bootstrap/modules.py` (one line) changed outside `modules/<name>/` and `tests/`.
- [ ] Public surface is only `application/contracts.py` (if any).
- [ ] Expected failures are `Err`; infra failures are typed exceptions chained with `from`.
- [ ] No blocking work on the UI thread; long work is a `JobContext` use case with progress + cancellation.
- [ ] UI contributions have unique, module-prefixed ids; strings in `strings.py`; accessible names on inputs/lists.
- [ ] `uv run python scripts/check.py` passes (format, lint, strict mypy, all tests).

## 10. Anti-patterns (reject these in your own output)

Business logic in widgets · domain importing logging/os/Qt · presentation importing domain · use case importing
`sqlite3`/`subprocess`/`os` · `Container` passed around · reading `os.environ` · `print()` · relative imports ·
`except Exception: pass` · `shell=True` · hard-coded paths · module reaching into another module's internals ·
adding a package to `shared/` · giant "manager"/"utils"/"helpers" classes · editing `MainWindow` to add a page ·
stylesheet strings in widgets · module-level mutable state · returning domain objects to the UI (use DTOs).

---------------------------------------------------------------------------------------------------

## 11. Task template (fill in and send together with this document)

```text
Task: Create module "<name>" that <one-sentence capability>.
Domain concepts: <entities / value objects / invariants>
Use cases: <actions, and which are long-running>
Needs from other modules (contracts): <none | module.contract>
Needs external tools/ports: <none | ProcessRunner for ffprobe | ...>
Persistence: <none | SQLite, own file>
UI: <page / dock / menu actions>
Deliver: full file contents for every new file (paths relative to repo root), the one-line change to
bootstrap/modules.py, and the tests from §8. Do not modify any other existing file.
```
