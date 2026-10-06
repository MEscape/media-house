"""Core: building blocks that are *not* a business module.

* ``domain``         - base classes for aggregates (pure Python).
* ``application``    - ports (interfaces) that use cases of any module may depend on.
* ``infrastructure`` - adapters for those ports (subprocess, system clock).
* ``modules``        - the module contract and the composition container.
"""
