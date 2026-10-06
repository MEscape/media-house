"""Bootstrap: the composition root. The only layer allowed to know every other layer.

* ``cli``         - argument parsing, process exit codes.
* ``application`` - headless startup/shutdown (no Qt). Testable without a display.
* ``composition`` - wires concrete adapters to abstract ports.
* ``desktop``     - creates the Qt application and the main window.
"""
