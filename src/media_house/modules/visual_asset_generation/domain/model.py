"""The stored ``visual_asset_set`` document: which library assets a batch produced, and how.

Same envelope as every Layer 4 plan (``plan_id, plan_version, supersedes, project_id, edit,
style, created_at, state, <items>, checks, inputs, stages, provenance, warnings, limitations,
schema_version``). The pictures themselves are ordinary library assets; this document only
names them (library ids), so nothing here duplicates the library or its derivation records.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from media_house.core.domain import Rational
from media_house.modules.visual_asset_generation.domain.qc import QcIssue

SCHEMA_VERSION = 1
#: Bump when the way a set is assembled changes (not the pixels: those have their own versions).
PLANNER_VERSION = 1
DOCUMENT_TYPE = "visual_asset_set"
SET_OPERATION = "visual_asset_set"
#: Derived-asset operations of this module (the cutout keeps its historic name).
ENHANCEMENT_OPERATION = "image_enhancement"
ENHANCEMENT_VERSION = 1
EXPORT_OPERATION = "image_export"
EXPORT_VERSION = 1
FRAME_GRAB_OPERATION = "frame_grab"
FRAME_GRAB_VERSION = 1


class ResultState(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


class InputHow(StrEnum):
    SUPPLIED = "supplied"
    REUSED = "reused"
    NOT_AVAILABLE = "not_available"


class StageState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"


class AssetKind(StrEnum):
    """What a produced library asset is."""

    FRAME = "frame"
    CLEAN_CUTOUT = "clean_cutout"
    ENHANCED = "enhanced"
    EXPORT = "export"


class ItemStatus(StrEnum):
    OK = "ok"
    #: Not usable; ``fallback_asset_id`` names what to use instead (never a pretend result).
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class EditRef:
    edit_plan_id: str
    plan_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleRef:
    """The visual style a set was made with (library document id + token fingerprint)."""

    #: ``None`` when the caller supplied a resolved style without its stored reference.
    document_id: str | None
    fingerprint: str
    identity_version: int
    profile: str


@dataclass(frozen=True, slots=True, kw_only=True)
class InputRecord:
    kind: str
    how: InputHow
    asset_id: str | None = None
    identity: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StageReport:
    stage: str
    state: StageState
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    schema_version: int
    planner_version: int
    profile: str
    profile_version: int
    configuration_fingerprint: str
    #: ``(name, version)`` of every engine whose output is named here.
    engines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameGrab:
    """A picture taken from a video for cutting out."""

    video_asset_id: str
    source_time: Rational
    frame_index: int | None
    frame_time: Rational | None
    exact: bool
    asset_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedAsset:
    """One produced (or reused) library asset of one request."""

    item_id: str
    request_id: str
    purpose: str
    variant: str
    kind: AssetKind
    status: ItemStatus
    #: The library asset to use. ``None`` only when the item failed and nothing could stand in.
    asset_id: str | None
    #: The ORIGINAL the request named (an image, or the video a frame was taken from).
    source_asset_id: str
    #: The direct parent in the library's derivation records.
    derived_from_asset_id: str | None
    clean_cutout_asset_id: str | None
    operation: str
    #: sha256 of the canonical configuration that produced the asset.
    configuration_hash: str
    width: int | None
    height: int | None
    format: str | None
    has_alpha: bool | None
    enhancements: tuple[str, ...] = ()
    reused: bool = False
    #: When ``status`` is FAILED: the safe stand-in (the clean cutout or the original).
    fallback_asset_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualAssetSet:
    plan_id: str
    plan_version: int
    supersedes: str | None
    project_id: str | None
    edit: EditRef | None
    style: StyleRef | None
    created_at: datetime
    state: ResultState
    items: tuple[PreparedAsset, ...]
    frames: tuple[FrameGrab, ...]
    checks: tuple[QcIssue, ...]
    inputs: tuple[InputRecord, ...]
    stages: tuple[StageReport, ...]
    provenance: Provenance
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def usable(self, request_id: str) -> tuple[PreparedAsset, ...]:
        return tuple(
            i for i in self.items if i.request_id == request_id and i.status is ItemStatus.OK
        )

    def asset_for(self, request_id: str, variant: str) -> str | None:
        """The asset to use for a request's variant: the variant, else its recorded fallback."""
        for item in self.items:
            if item.request_id == request_id and item.variant == variant:
                return item.asset_id if item.status is ItemStatus.OK else item.fallback_asset_id
        return None
