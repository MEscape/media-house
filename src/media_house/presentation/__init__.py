"""Presentation: the PySide6 adapter.

Rules (enforced by ``tests/architecture``):

* may import ``application`` layers and ``shared`` - never ``domain`` or ``infrastructure``;
* widgets render state and forward user intent; they contain no business rules;
* long-running work goes through :class:`~media_house.presentation.qt.job_bridge.UiJobRunner`;
* UI state lives in view models (``QObject`` + signals), not in widgets.
"""
