"""Processing provenance: the machine-readable record of what was done to a video.

Stored in the improved asset's metadata under ``METADATA_KEY`` so ANY module (Media Inspection,
an editor, a GUI) can read it through the Media Library without importing this module's
internals. It states facts: the source profile and how it was decided, the colour spaces
involved, every operation with its status, reason, parameters and the measured numbers behind
it, the engines and versions, whether Media Inspection supplied the facts, and before/after
measurements. Unknown or damaged records read as "no provenance", never as assumptions.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.video_improvement.domain.values import (
    METADATA_KEY,
    PROVENANCE_SCHEMA_VERSION,
    JsonValue,
    ProcessingStage,
    StageStatus,
)


@dataclass(frozen=True, slots=True)
class OperationRecord:
    stage: ProcessingStage
    name: str
    status: StageStatus
    reason: str
    parameters: Mapping[str, float | str | bool] = field(default_factory=dict)
    measured: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessingProvenance:
    source_asset_id: str
    source_profile: str
    source_profile_version: int
    #: How the source profile was decided (``explicit``, ``inspection``, ...) and on what evidence.
    source_profile_origin: str
    source_profile_evidence: str
    processing_profile: str
    input_color_space: str
    working_color_space: str
    output_color_space: str
    operations: tuple[OperationRecord, ...]
    engines: Mapping[str, str]
    processing_version: int
    #: ``inspection`` when Media Inspection supplied the source facts, ``probe`` when this module
    #: had to read the file itself.
    facts_source: str
    before: Mapping[str, float]
    after: Mapping[str, float]
    warnings: tuple[str, ...] = ()
    #: SHA-256 of the look LUT, empty when none was used.
    look_sha256: str = ""
    schema_version: int = PROVENANCE_SCHEMA_VERSION

    @property
    def inspection_used(self) -> bool:
        return self.facts_source == "inspection"

    def applied(self, stage: ProcessingStage) -> bool:
        return any(o.stage is stage and o.status is StageStatus.APPLIED for o in self.operations)

    def operation(self, name: str) -> OperationRecord | None:
        return next((o for o in self.operations if o.name == name), None)

    # --- storage ------------------------------------------------------------------------------
    def to_metadata(self) -> dict[str, JsonValue]:
        return {
            METADATA_KEY: {
                "schema_version": self.schema_version,
                "source_asset_id": self.source_asset_id,
                "source_profile": self.source_profile,
                "source_profile_version": self.source_profile_version,
                "source_profile_origin": self.source_profile_origin,
                "source_profile_evidence": self.source_profile_evidence,
                "processing_profile": self.processing_profile,
                "input_color_space": self.input_color_space,
                "working_color_space": self.working_color_space,
                "output_color_space": self.output_color_space,
                "operations": [
                    {
                        "stage": o.stage.value,
                        "name": o.name,
                        "status": o.status.value,
                        "reason": o.reason,
                        "parameters": dict(o.parameters),
                        "measured": dict(o.measured),
                    }
                    for o in self.operations
                ],
                "engines": dict(self.engines),
                "processing_version": self.processing_version,
                "facts_source": self.facts_source,
                "before": dict(self.before),
                "after": dict(self.after),
                "warnings": list(self.warnings),
                "look_sha256": self.look_sha256,
            }
        }

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, JsonValue]) -> "ProcessingProvenance | None":
        """The record in ``metadata``, or ``None`` when it is absent, damaged or from a newer
        schema (unknown means unknown)."""
        raw = metadata.get(METADATA_KEY)
        if not isinstance(raw, dict) or raw.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
            return None
        try:
            return cls(
                source_asset_id=_text(raw, "source_asset_id"),
                source_profile=_text(raw, "source_profile"),
                source_profile_version=_whole(raw, "source_profile_version"),
                source_profile_origin=_text(raw, "source_profile_origin"),
                source_profile_evidence=_text(raw, "source_profile_evidence"),
                processing_profile=_text(raw, "processing_profile"),
                input_color_space=_text(raw, "input_color_space"),
                working_color_space=_text(raw, "working_color_space"),
                output_color_space=_text(raw, "output_color_space"),
                operations=tuple(_operation(o) for o in _items(raw, "operations")),
                engines={str(k): str(v) for k, v in _mapping(raw, "engines").items()},
                processing_version=_whole(raw, "processing_version"),
                facts_source=_text(raw, "facts_source"),
                before=_numbers(raw, "before"),
                after=_numbers(raw, "after"),
                warnings=tuple(str(w) for w in _items(raw, "warnings")),
                look_sha256=_text(raw, "look_sha256"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _text(raw: Mapping[str, JsonValue], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise TypeError(key)
    return value


def _whole(raw: Mapping[str, JsonValue], key: str) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(key)
    return value


def _items(raw: Mapping[str, JsonValue], key: str) -> list[JsonValue]:
    value = raw[key]
    if not isinstance(value, list):
        raise TypeError(key)
    return value


def _mapping(raw: Mapping[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = raw[key]
    if not isinstance(value, dict):
        raise TypeError(key)
    return value


def _numbers(raw: Mapping[str, JsonValue], key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in _mapping(raw, key).items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(name)
        out[name] = float(value)
    return out


def _operation(item: JsonValue) -> OperationRecord:
    if not isinstance(item, dict):
        raise TypeError("operation")
    parameters: dict[str, float | str | bool] = {}
    for name, value in _mapping(item, "parameters").items():
        if not isinstance(value, float | int | str | bool):
            raise TypeError(name)
        parameters[name] = value
    return OperationRecord(
        stage=ProcessingStage(_text(item, "stage")),
        name=_text(item, "name"),
        status=StageStatus(_text(item, "status")),
        reason=_text(item, "reason"),
        parameters=parameters,
        measured=_numbers(item, "measured"),
    )
