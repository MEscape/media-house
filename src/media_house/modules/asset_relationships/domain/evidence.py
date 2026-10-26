"""Evidence: why a claim was made, small enough to read without loading an upstream analysis.

Evidence stores references and the few measured values that explain a claim. Embeddings,
transcripts and whole analyses stay with their owners.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

from media_house.modules.asset_relationships.domain.intervals import ClockOffset
from media_house.modules.asset_relationships.domain.values import EvidenceKind, JsonValue
from media_house.shared.errors import InvariantViolation

CLOCK_TRUST_UNVERIFIED: Final = "unverified"
CLOCK_TRUSTS: Final = frozenset({CLOCK_TRUST_UNVERIFIED, "same_device", "user_asserted"})
_SHA256_LENGTH: Final = 64
_OFFSET_KINDS = frozenset({EvidenceKind.WALL_CLOCK_OVERLAP, EvidenceKind.TIMECODE_OVERLAP})


def _is_json(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json(v) for k, v in value.items())
    return False


def _text(detail: Mapping[str, JsonValue], key: str) -> bool:
    value = detail.get(key)
    return isinstance(value, str) and bool(value.strip())


def _check_detail(kind: EvidenceKind, detail: Mapping[str, JsonValue]) -> str | None:
    """Why ``detail`` is not valid for ``kind``, or ``None``."""
    match kind:
        case EvidenceKind.CHECKSUM_MATCH:
            sha = detail.get("sha256")
            if not isinstance(sha, str) or len(sha) != _SHA256_LENGTH:
                return "sha256 must be a 64-character hex digest"
        case EvidenceKind.LIBRARY_DERIVATION:
            if not _text(detail, "operation") or not isinstance(detail.get("version"), int):
                return "operation (text) and version (integer) are required"
        case EvidenceKind.PROXY_PROVENANCE:
            if not all(_text(detail, key) for key in ("profile", "verdict", "evidence")):
                return "profile, verdict and evidence (text) are required"
        case EvidenceKind.WALL_CLOCK_OVERLAP:
            if detail.get("clock_trust") not in CLOCK_TRUSTS:
                return f"clock_trust must be one of {sorted(CLOCK_TRUSTS)}"
        case EvidenceKind.TIMECODE_OVERLAP:
            if not _text(detail, "timecode_source"):
                return "timecode_source (text) is required"
        case EvidenceKind.PRODUCTION_TAG_MATCH:
            if not all(_text(detail, key) for key in ("scene_a", "scene_b", "take_a", "take_b")):
                return "scene_a, scene_b, take_a and take_b (text) are required"
            for flag in ("same_device", "same_reel"):
                if flag in detail and not isinstance(detail[flag], bool):
                    return f"{flag} must be true or false"
        case EvidenceKind.USER_ASSERTION:
            if "note" in detail and not isinstance(detail["note"], str):
                return "note must be text"
    return None


@dataclass(frozen=True, slots=True)
class RelationshipEvidence:
    kind: EvidenceKind
    #: Who produced it (a detector id, ``user``) and which version of it.
    provider: str
    provider_version: str
    observed_at: datetime
    detail: Mapping[str, JsonValue] = field(default_factory=dict)
    #: The clock offset this evidence implies, when it implies one.
    offset: ClockOffset | None = None
    #: Replaced by newer evidence but kept because a person has decided the claim.
    superseded: bool = False

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.provider_version.strip():
            raise InvariantViolation("Evidence needs a provider and a provider version")
        if self.observed_at.tzinfo is None:
            raise InvariantViolation("Evidence needs an aware observation time")
        if not _is_json(dict(self.detail)):
            raise InvariantViolation("Evidence detail must be JSON-safe")
        problem = _check_detail(self.kind, self.detail)
        if problem is not None:
            raise InvariantViolation(
                f"Invalid {self.kind.value} evidence: {problem}", details={"kind": self.kind.value}
            )
        if self.offset is not None and self.kind not in _OFFSET_KINDS:
            raise InvariantViolation(f"{self.kind.value} evidence cannot carry a clock offset")
        object.__setattr__(self, "detail", dict(self.detail))

    @property
    def key(self) -> str:
        """Names the evidence in a decision record."""
        return f"{self.kind.value}|{self.provider}|{self.provider_version}"

    def same_content(self, other: "RelationshipEvidence") -> bool:
        """Equal in everything but when it was observed and whether it was superseded."""
        return (
            self.kind is other.kind
            and self.provider == other.provider
            and self.provider_version == other.provider_version
            and dict(self.detail) == dict(other.detail)
            and self.offset == other.offset
        )

    def as_superseded(self) -> "RelationshipEvidence":
        return replace(self, superseded=True)
