"""Minimal dependency-injection container for the *composition root only*.

This is not a service locator. Business classes receive their collaborators
through constructors; the container exists solely so that ``bootstrap`` and each
module's ``module.py`` can describe *how to build* those collaborators.
An architecture test forbids importing this module from domain, application,
infrastructure or presentation code.

Features (and nothing more): lazy singletons, ready-made instances,
multi-bindings (``add_to_collection``), cycle detection, clear errors.
"""

import threading
from collections.abc import Callable
from typing import Any

from media_house.shared.errors import ConfigurationError

type Factory[T] = Callable[["Container"], T]


class DependencyError(ConfigurationError):
    """Wiring is wrong: a service is missing, duplicated or part of a cycle."""

    code = "configuration.dependency"


class Container:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[type, Factory[Any]] = {}
        self._instances: dict[type, object] = {}
        self._collections: dict[type, list[Factory[Any]]] = {}
        self._collection_cache: dict[type, list[object]] = {}
        self._resolving: list[type] = []

    def register_instance[T](self, key: type[T], instance: T) -> None:
        with self._lock:
            self._ensure_free(key)
            self._instances[key] = instance

    def register_factory[T](self, key: type[T], factory: Factory[T]) -> None:
        """Register a lazily created singleton."""
        with self._lock:
            self._ensure_free(key)
            self._factories[key] = factory

    def add_to_collection[T](self, key: type[T], factory: Factory[T]) -> None:
        """Contribute one item to a multi-binding (e.g. every module's UI contributor)."""
        with self._lock:
            if key in self._collection_cache:
                raise DependencyError(f"Collection {key.__name__} was already resolved")
            self._collections.setdefault(key, []).append(factory)

    def resolve[T](self, key: type[T]) -> T:
        with self._lock:
            if key in self._instances:
                return self._instances[key]  # type: ignore[return-value]
            factory = self._factories.get(key)
            if factory is None:
                raise DependencyError(f"No service registered for {key.__name__}")
            if key in self._resolving:
                chain = " -> ".join(k.__name__ for k in [*self._resolving, key])
                raise DependencyError(f"Circular dependency: {chain}")
            self._resolving.append(key)
            try:
                instance = factory(self)
            finally:
                self._resolving.pop()
            self._instances[key] = instance
            return instance  # type: ignore[no-any-return]

    def resolve_all[T](self, key: type[T]) -> list[T]:
        with self._lock:
            if key not in self._collection_cache:
                self._collection_cache[key] = [f(self) for f in self._collections.get(key, [])]
            return list(self._collection_cache[key])  # type: ignore[arg-type]

    def _ensure_free(self, key: type) -> None:
        if key in self._instances or key in self._factories:
            raise DependencyError(f"{key.__name__} is already registered")
