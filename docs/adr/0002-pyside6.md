# ADR-0002: PySide6 as the UI adapter

Status: accepted

**Context.** Needs a mature, cross-platform desktop toolkit with dock widgets, Model/View, rich theming and
good support for background work and media-oriented widgets later.

**Decision.** PySide6 (official Qt for Python, LGPL), specifically `PySide6-Essentials` (QtCore/Gui/Widgets
without large optional add-ons). Qt is confined to `presentation/` and each module's `presentation/` + `module.py`.
View models are `QObject`s with signals; widgets are passive.

**Alternatives.** Tkinter (limited widgets/theming/docking, weak Model/View); PyQt6 (GPL/commercial licence);
web UI in a shell (second runtime and language, IPC boundary).

**Consequences.** + Mature widgets, docking, accessibility hooks. + LGPL. - Qt threading rules must be respected
(see ADR-0006). - Qt leaks into `module.py` imports (ADR-0010).
