"""Minimal Result type for *expected* operational outcomes.

Use ``Result`` where failure is a normal, anticipated outcome the caller must
handle (duplicate name, invalid input). Use exceptions for everything else
(disk failure, bug, missing tool).

    match use_case.execute(command):
        case Ok(dto): ...
        case Err(error): ...
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E


type Result[T, E] = Ok[T] | Err[E]
