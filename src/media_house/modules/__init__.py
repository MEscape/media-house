"""Feature modules: vertical slices with their own domain, application, infrastructure, UI.

Modules must not import each other's internals; the only cross-module surface is
``modules.<name>.application.contracts``. See ARCHITECTURE.md.
"""
