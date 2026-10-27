"""The execution DAG. Pure: no clock, no threads. The runner asks it what is ready."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from media_house.modules.media_pipeline.domain.values import Lane, TaskId
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class TaskSpec:
    id: TaskId
    lane: Lane
    #: Upstream tasks whose result this one cannot run without.
    needs: frozenset[TaskId] = frozenset()
    #: Upstream tasks this one only benefits from: it waits for them but runs whatever they do.
    wants: frozenset[TaskId] = frozenset()

    @property
    def upstream(self) -> frozenset[TaskId]:
        return self.needs | self.wants


class PipelineGraph:
    """A validated set of tasks: known dependencies, no cycles, deterministic order."""

    def __init__(self, specs: Iterable[TaskSpec]) -> None:
        self._specs: dict[TaskId, TaskSpec] = {}
        for spec in specs:
            if spec.id in self._specs:
                raise InvariantViolation(f"Duplicate task {spec.id}")
            self._specs[spec.id] = spec
        for spec in self._specs.values():
            for dep in sorted(spec.upstream):
                if dep not in self._specs:
                    raise InvariantViolation(f"Task {spec.id} depends on unknown task {dep}")
            if spec.id in spec.upstream:
                raise InvariantViolation(f"Task {spec.id} depends on itself")
        self._order = self._topological_order()
        self._downstream = self._build_downstream()
        self._weight = self._build_weights()

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._specs

    def __getitem__(self, task_id: TaskId) -> TaskSpec:
        return self._specs[task_id]

    @property
    def order(self) -> tuple[TaskId, ...]:
        """A topological order; ties keep insertion order, so equal inputs give equal plans."""
        return self._order

    def weight(self, task_id: TaskId) -> int:
        """How many tasks transitively wait for this one. Heavier tasks start first."""
        return self._weight[task_id]

    def levels(self) -> tuple[tuple[TaskId, ...], ...]:
        """Tasks grouped by longest dependency chain: everything in one row is independent."""
        depth: dict[TaskId, int] = {}
        for tid in self._order:
            ups = self._specs[tid].upstream
            depth[tid] = 1 + max((depth[u] for u in ups), default=-1)
        rows: dict[int, list[TaskId]] = {}
        for tid in self._order:
            rows.setdefault(depth[tid], []).append(tid)
        return tuple(tuple(rows[d]) for d in sorted(rows))

    def downstream_of(self, task_id: TaskId) -> frozenset[TaskId]:
        return self._downstream[task_id]

    def _topological_order(self) -> tuple[TaskId, ...]:
        done: set[TaskId] = set()
        order: list[TaskId] = []
        visiting: list[TaskId] = []

        def visit(tid: TaskId) -> None:
            if tid in done:
                return
            if tid in visiting:
                cycle = [*visiting[visiting.index(tid) :], tid]
                raise InvariantViolation(
                    "Dependency cycle: " + " -> ".join(str(t) for t in cycle),
                    details={"cycle": [str(t) for t in cycle]},
                )
            visiting.append(tid)
            for dep in sorted(self._specs[tid].upstream):
                visit(dep)
            visiting.pop()
            done.add(tid)
            order.append(tid)

        for tid in self._specs:
            visit(tid)
        return tuple(order)

    def _build_downstream(self) -> Mapping[TaskId, frozenset[TaskId]]:
        direct: dict[TaskId, set[TaskId]] = {tid: set() for tid in self._specs}
        for spec in self._specs.values():
            for dep in spec.upstream:
                direct[dep].add(spec.id)
        closure: dict[TaskId, frozenset[TaskId]] = {}
        for tid in reversed(self._order):
            acc: set[TaskId] = set()
            for child in direct[tid]:
                acc.add(child)
                acc |= closure[child]
            closure[tid] = frozenset(acc)
        return closure

    def _build_weights(self) -> Mapping[TaskId, int]:
        return {tid: len(down) for tid, down in self._downstream.items()}
