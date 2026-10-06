# ADR-0001: Modular monolith with hexagonal architecture

Status: accepted

**Context.** Media-House will grow into many capabilities (transcription, rendering, timelines, AI, ...) built by
several engineers over years. It is a single-user desktop app, so network boundaries between parts add cost and no benefit.
UI toolkits, databases and external tools (FFmpeg, AI runtimes) will change; the business model should not.

**Decision.** One process, one deployable. Code is split into feature *modules* (vertical slices) with
`domain / application / infrastructure / presentation` layers. Dependencies point inward; the domain imports
(almost) nothing. External capabilities are *ports* (`typing.Protocol`) implemented by *adapters*; wiring happens
only in composition roots (`bootstrap/`, each `module.py`). Modules talk through
`application/contracts.py`. The rules are enforced by tests (ADR-0009).

**Alternatives.** (a) Layered-by-technology packages (`models/`, `views/`, `services/`) - simplest, but couples every
feature to every other and hides where code belongs. (b) Microservices/plugin processes - unjustified operational cost
for a desktop app. (c) Plain MVC with Qt models as the domain - fastest start, but business logic ends up in widgets.

**Consequences.** + Clear answer to "where does this code go". + Domain/use cases testable without Qt, DB, tools.
+ Modules can be added without touching others. - More files and indirection than a script-style app;
mitigated by *not* creating layers/ports a module does not need.
