"""Shared kernel: small, stable, cross-cutting primitives only.

Rules (enforced by ``tests/architecture``):

* may import the standard library, ``pydantic`` and ``platformdirs`` only;
* must never import ``core``, ``modules``, ``presentation`` or ``bootstrap``;
* must never import Qt;
* contains no business logic.
"""
