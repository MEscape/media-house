# ADR-0010: UI contributed through a registry; module.py may import Qt

Status: accepted

**Context.** The main window must not know every module, yet modules must add pages, menu actions and docks.

**Decision.** `presentation/extension` defines contribution descriptors (`ActionContribution`, `ViewContribution`,
`DockContribution`), a `UiRegistry`, and the `UiContributor` protocol. A module's `module.py` adds a lazily built
contributor to a container multi-binding; the desktop bootstrap collects them and the `MainWindow` renders the
registry. The shell contributes its own actions/docks through the same mechanism.

**Alternatives.** The window imports each module (violates open/closed); a global event/plugin registry (hidden
coupling, import-time side effects).

**Consequences.** + Adding a module never edits the shell. - `UiContributor` references Qt types, so
`module.py` (a composition file) imports Qt, and headless `--check` loads Qt libraries (not a display).
Accepted: only `module.py` and `presentation/` do so; domain/application/infrastructure stay Qt-free (tested).
