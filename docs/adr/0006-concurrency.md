# ADR-0006: Thread-pool jobs + Qt dispatcher; no asyncio for now

Status: accepted

**Context.** The UI thread must never block. Expected workloads: disk IO, SQLite, external processes (FFmpeg),
network later. Heavy CPU work will run in external tools.

**Decision.** `shared/concurrency` defines Qt-free job primitives (`JobContext` with progress + cooperative
cancellation, `JobHandle`, `JobState`, outcome) and a `ThreadPoolJobScheduler`. Work is plain
`work(JobContext) -> T`. `presentation/qt/UiJobRunner` is the only bridge: submit on the UI thread, callbacks
and signals delivered on the UI thread through `UiDispatcher` (queued signal). Processes are run through the
`ProcessRunner` port, which supports cancel/timeout. Failures become `ErrorReport`s.

**Alternatives.** QThread/QRunnable everywhere (ties use cases to Qt); asyncio (a second event loop next to Qt's,
needs qasync-style glue, no current workload that benefits); multiprocessing (not needed until pure-Python CPU work exists).

**Consequences.** + Use cases are testable with an immediate fake scheduler. + One audited thread-crossing point.
- Cancellation is *cooperative*: non-cooperative blocking code cannot be stopped and, because pool threads are
non-daemon, would delay interpreter exit after shutdown timeout. Wrap such work in a subprocess (killable).
