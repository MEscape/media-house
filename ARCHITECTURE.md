# Media-House Architecture

> The most important property of this codebase: a developer can tell **where code belongs and why**.

Media-House is a **modular monolith**: one process, one deployable, many independently understandable modules.
It uses **hexagonal architecture** (ports & adapters) and **pragmatic DDD**. The rules below are not aspirations -
`tests/architecture` fails the build when they are broken.

Decisions and alternatives are recorded in [docs/adr](docs/adr/README.md).

---

## 1. Layers and the dependency rule

```text
            ┌──────────────────────────────┐
            │         Presentation         │  PySide6: widgets, view models, shell
            └──────────────┬───────────────┘
                           │ uses
            ┌──────────────▼───────────────┐
            │          Application         │  use cases, commands/queries, DTOs, ports
            └──────────────┬───────────────┘
                           │ uses
            ┌──────────────▼───────────────┐
            │            Domain            │  entities, value objects, invariants, events
            └──────────────▲───────────────┘
                           │ implements ports of
            ┌──────────────┴───────────────┐
            │        Infrastructure        │  SQLite, subprocess, filesystem, clock
            └──────────────────────────────┘

   bootstrap (composition root) sees everything and is the only place that wires adapters to ports.
```

| Layer | May import | Must never import |
| --- | --- | --- |
| **domain** | stdlib (minus `os`, `logging`, `sqlite3`, `subprocess`, `threading`, …), `shared.errors/events/types` | Qt, application, infrastructure, presentation, logging, config, filesystem |
| **application** | own domain, `core.domain`, `core.application` (ports), `shared.*` | infrastructure, presentation, Qt, `sqlite3`, `subprocess`, `os` |
| **infrastructure** | domain, application, `shared.*` | presentation, Qt |
| **presentation** | application, `shared.*`, the shell's `presentation.*` | **domain**, **infrastructure**, other modules |
| **module.py** | its own four layers, `core.modules`, `presentation.extension` | other modules' internals |
| **bootstrap** | everything | - (and nothing imports bootstrap) |

The presentation layer deliberately cannot see the domain: it works with DTOs and use cases, so a UI can never
bypass an invariant or depend on how the model is built.

Qt lives in `presentation/`, each module's `presentation/` and `module.py`. It is an *adapter driving the application*,
not part of the core.

## 2. Package map

```text
src/media_house/
├── bootstrap/          # composition root (the only layer that knows everything)
│   ├── cli.py          #   argument parsing, exit codes (--check, --version, --home, ...)
│   ├── application.py  #   headless Application.start()/shutdown(): phases, no Qt
│   ├── composition.py  #   builds adapters + container; the ports→adapters table
│   ├── desktop.py      #   QApplication, error boundary, theme, main window, event loop
│   ├── environment.py  #   startup validation (writable directories)
│   ├── lifecycle.py    #   StartupPhase, StartupError, ShutdownStack
│   └── modules.py      #   the product's module list (one line per module)
├── shared/             # SMALL cross-cutting kernel (see §9)
│   ├── errors/         #   taxonomy, Result, ErrorHandler
│   ├── logging/        #   structured logging, correlation ids, redaction
│   ├── configuration/  #   typed settings + layered loader
│   ├── filesystem/     #   AppPaths, path-traversal-safe resolve_within
│   ├── concurrency/    #   JobContext/JobHandle/CancellationToken + ThreadPoolJobScheduler
│   ├── events/         #   EventBus, DomainEvent, ApplicationEvent
│   └── types/          #   id generation
├── core/               # things that belong to no feature
│   ├── domain/         #   AggregateRoot
│   ├── application/ports/   # Clock, ProcessRunner (+ ProcessSpec/Result)
│   ├── infrastructure/ #   SystemClock, SubprocessRunner (the only `import subprocess`)
│   └── modules/        #   ApplicationModule contract + Container (composition-only)
├── modules/
│   └── workspace/      # EXAMPLE module - shows the shape, is not product functionality
│       ├── domain/     #   WorkspaceName/Id (value objects), Workspace (aggregate), events, repository port
│       ├── application/#   CreateWorkspace, ListWorkspaces, VerifyWorkspaces, DTOs, commands, contracts.py
│       ├── infrastructure/  # SqliteWorkspaceRepository
│       ├── presentation/    # WorkspaceViewModel, WorkspaceView, list model, contributor
│       └── module.py   #   registration (the module's own composition root)
└── presentation/       # the shell
    ├── application_window/  # MainWindow, menus, lazy navigation (thin; renders contributions)
    ├── extension/      #   Action/View/DockContribution, UiRegistry, UiContributor
    ├── qt/             #   UiDispatcher, UiJobRunner, GlobalErrorBoundary, WindowStateStore, StatusReporter
    ├── styling/ + resources/  # ThemeManager, base.qss, icons - the only place styles live
    └── core_ui.py      #   the shell contributes quit/theme/about/jobs through the same extension points
```

Deviations from the proposed template, with reasons: `shared/` has no `result`/`types` explosion (kept to seven
packages and a test caps it); there is no global `presentation/viewmodels` or `dialogs` zoo - view models belong to
the module that owns the behaviour; the example module's `domain/` is flat files (`values.py`, `events.py`, ...)
because sub-packages with one file each add nothing; `core/` gained `infrastructure/` and `modules/` because ports
need adapters and the module contract needs a home that both `bootstrap` and `module.py` can import.

## 3. Ports and adapters

A port is a `typing.Protocol` owned by the layer that *needs* the capability.

| Port | Owner | Adapter(s) | Notes |
| --- | --- | --- | --- |
| `WorkspaceRepository` | workspace domain | `SqliteWorkspaceRepository`; in-memory fake in tests | contract tests run against both |
| `ProcessRunner` | `core.application.ports` | `SubprocessRunner` | no shell; list argv; timeout; cancel; streaming; typed errors |
| `Clock` | `core.application.ports` | `SystemClock`; `FixedClock` in tests | the domain never reads the clock |
| `JobScheduler` | `shared.concurrency` | `ThreadPoolJobScheduler`; `ImmediateJobScheduler` in tests | |
| `EventPublisher` | `shared.events` | `EventBus` | |
| `UiContributor` | `presentation.extension` | one per module | |

`ProcessRunner` is where FFmpeg/FFprobe/AI CLIs will plug in. A missing tool surfaces as `ToolNotFoundError`
with an actionable user message; a hung tool is stopped by timeout or cancellation; stderr stays in logs/details,
never in user messages.

## 4. Modules and their boundaries

* A module is a vertical slice: `domain/ application/ infrastructure/ presentation/ module.py`. Omit layers you do not need.
* Treat internals as private. The **only** cross-module surface is `modules.<name>.application.contracts`
  (small, stable `Protocol`s + DTOs). `workspace.application.contracts.WorkspaceCatalog` shows the pattern.
* Modules never touch each other's repositories, entities, widgets or databases.
* Cross-module reactions go through the `EventBus` (domain/application events), not direct calls into internals.
  Prefer a direct call through a contract when it is clearer - not everything is an event.

### Adding a module (checklist)

1. `modules/<name>/domain` first, with Qt-free unit tests.
2. Use cases in `application/`; DTOs for anything the UI sees; `contracts.py` only if others need you.
3. Adapters in `infrastructure/`, verified by contract/integration tests.
4. `presentation/` view model(s) + views + a `UiContributor`.
5. `module.py`: register services and `container.add_to_collection(UiContributor, ...)`.
6. One line in `bootstrap/modules.py`.
7. Run `scripts/check.py`. You should not have edited the shell, `shared/`, or another module.

## 5. Composition root, dependency injection, lifecycle

Constructor injection everywhere. There is no global service locator and no singleton objects hidden in modules.
The small `Container` (lazy singletons, instances, multi-bindings, cycle detection) exists *only* so `bootstrap`
and each `module.py` can describe how to build things; an architecture test forbids importing it anywhere else.

```text
cli.main ──► Application.start()                         (headless, no Qt)
               1 CONFIGURATION   resolve paths, load + validate settings   (fail early)
               2 LOGGING         configure rotating JSON / console logging
               3 ENVIRONMENT     create + probe-write directories
               4 INFRASTRUCTURE  error handler, event bus, job scheduler, process runner, clock
               5 SERVICES        container: core services, then every module.register()
           ──► run_desktop()                              (Qt)
               6 PRESENTATION    dispatcher, job bridge, error boundary, theme, registry, window
               run               QApplication.exec()
               shutdown          aboutToQuit → save window state → cancel/join jobs → flush logs
```

Any failure becomes a `StartupError(phase, cause)` that keeps the cause's reference id and user message.
`--check` runs phases 1-5 without a window (used in CI and for support: exit code `0` or `2`).
`ShutdownStack` runs cleanup LIFO, logs and continues past a failing step, and is idempotent.

## 6. Errors and results

```text
MediaHouseError (category, code, user_message, details, error_id)
├── DomainError ── InvariantViolation
├── ValidationError
├── ApplicationError ── NotFoundError · ConflictError · OperationCancelledError
├── InfrastructureError ── PersistenceError
│   └── ExternalSystemError ── ToolNotFoundError · ProcessTimeoutError · ProcessFailedError
├── ConfigurationError            (not recoverable)
└── UnexpectedError               (wraps anything else; always chained with `from`)
```

* **Expected outcomes** (invalid name, duplicate) are returned: `Result[T, E] = Ok | Err`. Callers must handle them.
* **Unexpected failures** raise. Always `raise X(...) from exc`; never swallow.
* `ErrorHandler.handle(exc)` is the single place errors are logged (INFO for expected categories; ERROR **with
  traceback** for the rest) and turned into an `ErrorReport` (id, safe message, recoverable flag, one-line technical summary).
* Boundaries: job scheduler (failure → `JobOutcome.FAILED` + report), `GlobalErrorBoundary` (uncaught exceptions in
  slots and threads → dialog with reference id; app keeps running), startup (`StartupError`).
* Users never see tracebacks or paths; the log has everything, found by `error_id`.

## 7. State

| State | Lives in | Notes |
| --- | --- | --- |
| Domain | aggregates, loaded/saved through repositories | invariants enforced in constructors/methods |
| Application | use-case locals; job state in `JobHandle` | no global mutable state |
| Presentation | view models (`QObject` + signals): busy flags, form errors, progress | owned by the module that owns the page |
| Widget | Qt widgets (text in a line edit, selection) | never the source of truth |

Data flows `Widget → ViewModel intent → UiJobRunner → use case → repository` and back as signals carrying DTOs.
There is deliberately no Redux-style store; add one only if cross-page state sharing becomes real.

## 8. Concurrency

```text
UI thread ──submit──► UiJobRunner ──► JobScheduler (thread pool) ──► work(JobContext) ──► adapters
    ▲                                         │ callbacks (worker thread)
    └──────── UiDispatcher (queued signal) ◄──┘   results, progress, errors delivered on the UI thread
```

* Work is plain `work(JobContext) -> T`; it reports progress via `ctx.progress` and polls `ctx.raise_if_cancelled()`.
* States: `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`; exactly one terminal outcome per job.
* `UiJobRunner.submit` must be called on the UI thread (enforced, raises otherwise). Worker threads never touch widgets.
* Correlation ids (`contextvars`) are copied to the worker so log lines for one action line up.
* Shutdown cancels running/pending jobs, waits up to a configured timeout, and rejects new submissions.
* External processes run through `ProcessRunner`; cancellation/timeouts terminate the child, then kill after a grace period.
* asyncio is intentionally absent (ADR-0006).

## 9. The shared kernel (guard rails)

`shared/` may import only stdlib, `pydantic`, `platformdirs`, and itself. It holds seven packages; adding an eighth
fails an architecture test and needs an ADR. It contains mechanisms, never business rules. If something is used by
two modules, first ask whether it is a *port* (→ `core`) or a *contract* (→ one module's `contracts.py`).

## 10. Configuration, logging, filesystem, security

* **Configuration**: typed models, strict (`extra="forbid"`), layered, validated at startup; `os.environ` read once;
  secrets only from `MEDIA_HOUSE_SECRET_*` as `SecretStr`, never from the settings file. See README.
* **Logging**: `get_logger(__name__).info("msg", key=value)`; JSON-lines rotating file; correlation/operation ids;
  sensitive field names (`password`, `token`, `api_key`, …) redacted. Do not put secrets in messages.
* **Filesystem**: all locations come from `AppPaths` (Windows/macOS/Linux via `platformdirs`); `--home` gives a
  portable layout; `resolve_within(root, user_path)` blocks `..`, absolute paths and symlink escapes;
  `temporary_directory()` guarantees cleanup.
* **Security baseline**: no `shell=True` (argv lists only; ruff + architecture test confine `subprocess`);
  stdin closed for child processes; lenient decoding of tool output; output memory-bounded; ruff `S` rules on;
  `Container` and `os.environ` access are policed.

## 11. UI architecture

* The shell renders **contributions** (actions, pages, docks) from a `UiRegistry`; modules never edit the main window,
  and the shell never names a module. The shell itself uses the same mechanism (`core_ui.py`).
* Pages are created lazily on first visit; menus are ordered deterministically (File, View, others, Help).
* Styling: one `base.qss` template + `ThemeTokens` (light/dark/system, switchable at runtime). Widgets carry no
  stylesheet strings.
* Accessibility: accessible names on inputs/lists, keyboard shortcuts for actions, visible focus rings in the theme,
  tab order set on forms, text-based status feedback (not colour alone), high-contrast token pairs.
* i18n: user-facing strings are collected in `strings.py` files (one for the shell, one per module) - ready to wrap with
  `QCoreApplication.translate` later.
* Resources (icons, QSS) are package data under `presentation/resources/` and are verified to ship in the wheel.

## 12. Testing strategy

| Suite | Needs | What it proves | Speed |
| --- | --- | --- | --- |
| `tests/unit` | nothing (no Qt/files/network) | domain rules, use cases (with fakes), errors, config, logging, container, jobs | ~1 s |
| `tests/architecture` | source files | dependency rules hold; the rules themselves catch violations; no cycles | <1 s |
| `tests/integration` | `tmp_path`, SQLite, child processes | adapters honour their ports (contract tests), startup/shutdown, CLI | ~1 s |
| `tests/presentation` | offscreen Qt | thread marshalling, view model/view behaviour, shell assembly, error boundary, full launch in a subprocess | ~2 s |

`CreateWorkspace(...)` can be tested with `InMemoryWorkspaceRepository` + `FixedClock` + `RecordingPublisher`:
no Qt, no filesystem, no environment. Warnings are errors; this already caught a real resource leak.

## 13. Critical architecture review

Performed after the first complete implementation. **Fixed during the review** are things the review found and corrected.

**Fixed**
* *Concurrency*: `UiJobRunner.submit` could be called from a worker thread (silent race) → now enforced + tested.
* *Boundary leak*: ErrorHandler/`errors` import cycle (`errors → logging → configuration → errors`) → `ErrorHandler`
  moved to `errors.handler` and not re-exported; cycle test guards it.
* *Test fidelity*: the in-memory repository returned live aggregates (with pending events), unlike a real adapter →
  now rehydrates copies; contract tests run against both.
* *Startup UX*: raw log records leaked to the terminal before logging was configured → `NullHandler` convention.
* *Quit persistence*: window layout was only saved on close events → also saved on `aboutToQuit`.
* *Menu ordering*: a dock contributed without a View action created "View" after "Help" → required menus are created up front.

**Abstractions that might be unnecessary (watch them)**
* `ApplicationEvent` has one user (`WorkspaceVerificationCompleted`). It documents the distinction from domain
  events; delete if no second use appears.
* `Container.add_to_collection` is used once. It is what lets modules contribute UI without the shell knowing them.
* `ThemeTokens` + QSS template is more than two colours need today; it earns its keep when the third theme or
  custom branding arrives.

**Coupling / boundary risks**
* `module.py` imports Qt (via `UiContributor`), so headless `--check` loads Qt libraries (no display needed).
  Accepted in ADR-0010; if startup time or a non-GUI front-end (CLI/server mode) ever matters, split `module.py`
  into `register()` (headless) and `ui.py`.
* Presentation view models call concrete use-case classes. Fine inside a module; tests inject fakes through the
  repository port. Introduce use-case protocols only if presentation tests need them.
* The architecture analyzer cannot see dynamic imports; reviewers must reject `importlib` tricks.

**Scalability**
* SQLite adapter opens a connection per operation: correct and simple, not optimal for bulk writes. Batch methods /
  a unit-of-work abstraction should be added with the first real multi-aggregate transaction. Alembic when the schema first changes.
* `EventBus` is synchronous and in-process: handlers run on the publisher's thread (often a worker). Handlers must be
  quick; anything slow should submit a job. No persistence/replay.
* `shared/` will attract pressure. The seven-package cap test is the guard; keep saying no.

**Testing gaps**
* No tests on real Windows/macOS (path and process semantics, `terminate` vs process trees).
* UI tests run offscreen: no pixel/layout/accessibility-tool verification (screen readers untested).
* Property-based tests for `resolve_within` and name validation would be a cheap, valuable addition.

**Concurrency risks**
* Cancellation is cooperative. Non-cooperative blocking work cannot be interrupted and (non-daemon pool threads)
  can delay process exit after the shutdown timeout. Run such work in a subprocess.
* `SubprocessRunner` terminates the direct child only, not its process tree (documented in the module).
* The job list keeps at most 50 rows; long-lived job history/telemetry is out of scope.

**Error-handling risks**
* `ErrorHandler` logs `details` verbatim; redaction is by key name only. Keep secrets out of exception messages and details.
* Pre-logging startup failures have no log file (only the CLI/dialog message with reference id and cause); acceptable
  because nothing has happened yet, but a bootstrap fallback log file could help support.

**Likely future technical debt**
* Settings UI + writing `settings.toml` (today read-only).
* Credential storage via OS keyring (secrets are env-only today).
* Real i18n pipeline; accessibility audit; packaging (PyInstaller/Briefcase) and code signing.
* A real `FileStorage`/media-location abstraction when media directories appear (intentionally not built: no consumer yet).
* The example module's demonstration pacing delay (`_DEMO_STEP_DELAY_SECONDS`) - remove with the module.
