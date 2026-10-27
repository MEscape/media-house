"""What a run tells its caller: typed, complete, and serialisable. Reused work is reported as
reused, never as new work, and every non-run has a reason."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.media_library.application.contracts import JsonValue
from media_house.modules.media_pipeline.domain.values import Lane, TaskKind, TaskStatus

REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TaskReport:
    kind: TaskKind
    #: ``None`` only in a dry run, where nothing was executed.
    status: TaskStatus | None
    lane: Lane | None = None
    detail: str = ""
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, JsonValue] = field(default_factory=dict)
    #: Task kinds of the same asset this one needs / waits for.
    needs: tuple[TaskKind, ...] = ()
    wants: tuple[TaskKind, ...] = ()
    #: Added to the run only because another requested task needs it.
    implied: bool = False
    queued_seconds: float = 0.0
    run_seconds: float = 0.0
    #: Time in the pre-flight cache check, when the task has one.
    lookup_seconds: float = 0.0
    #: ``preflight_hit`` (served before queueing), ``owner_hit`` (the owner found it stored while
    #: running), ``miss`` (computed), or empty when caching does not apply to the outcome.
    cache: str = ""
    #: Seconds since the run started; shows which tasks overlapped.
    started_at: float | None = None
    finished_at: float | None = None


@dataclass(frozen=True, slots=True)
class AssetReport:
    asset_id: str
    name: str
    media_type: str
    #: ``imported`` / ``existing`` for a path; ``given`` for an asset id.
    origin: str
    tasks: tuple[TaskReport, ...] = ()
    notes: tuple[str, ...] = ()

    def task(self, kind: TaskKind) -> TaskReport | None:
        return next((t for t in self.tasks if t.kind is kind), None)


@dataclass(frozen=True, slots=True)
class InputFailure:
    """An input that never became an asset (unreadable path, unsupported file)."""

    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class PipelineReport:
    assets: tuple[AssetReport, ...]
    failures: tuple[InputFailure, ...] = ()
    dry_run: bool = False
    cancelled: bool = False
    wall_seconds: float = 0.0
    #: Tasks that could run at the same time, level by level (the execution graph).
    levels: tuple[tuple[str, ...], ...] = ()
    lane_capacity: Mapping[Lane, int] = field(default_factory=dict)
    peak_running: Mapping[Lane, int] = field(default_factory=dict)
    peak_total: int = 0

    def cache_counts(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for asset in self.assets:
            for task in asset.tasks:
                if task.cache:
                    counts[task.cache] = counts.get(task.cache, 0) + 1
        return counts

    def counts(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for asset in self.assets:
            for task in asset.tasks:
                key = task.status.value if task.status else "planned"
                counts[key] = counts.get(key, 0) + 1
        return counts

    @property
    def ok(self) -> bool:
        """No input failed, nothing failed, nothing was cancelled. Skips are not failures."""
        counts = self.counts()
        return not self.failures and not self.cancelled and counts.get("failed", 0) == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, JsonValue]:
        def task(t: TaskReport) -> dict[str, JsonValue]:
            return {
                "kind": t.kind.value,
                "status": t.status.value if t.status else None,
                "lane": t.lane.value if t.lane else None,
                "detail": t.detail,
                "warnings": list(t.warnings),
                "facts": dict(t.facts),
                "needs": [k.value for k in t.needs],
                "wants": [k.value for k in t.wants],
                "implied": t.implied,
                "queued_seconds": round(t.queued_seconds, 3),
                "run_seconds": round(t.run_seconds, 3),
                "lookup_seconds": round(t.lookup_seconds, 3),
                "cache": t.cache,
                "started_at": None if t.started_at is None else round(t.started_at, 3),
                "finished_at": None if t.finished_at is None else round(t.finished_at, 3),
            }

        def asset(a: AssetReport) -> dict[str, JsonValue]:
            return {
                "asset_id": a.asset_id,
                "name": a.name,
                "media_type": a.media_type,
                "origin": a.origin,
                "notes": list(a.notes),
                "tasks": [task(t) for t in a.tasks],
            }

        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "ok": self.ok,
            "dry_run": self.dry_run,
            "cancelled": self.cancelled,
            "wall_seconds": round(self.wall_seconds, 3),
            "counts": dict(self.counts()),
            "cache": dict(self.cache_counts()),
            "lane_capacity": {k.value: v for k, v in self.lane_capacity.items()},
            "peak_running": {k.value: v for k, v in self.peak_running.items()},
            "peak_total": self.peak_total,
            "levels": [list(level) for level in self.levels],
            "failures": [{"source": f.source, "reason": f.reason} for f in self.failures],
            "assets": [asset(a) for a in self.assets],
        }
