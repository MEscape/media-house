"""Limited image generation: the request, its provenance record and the policy that gates it.

Generation is the last resort. It is allowed only for illustrative material that does not claim
to show something real, only when the need is stated and only when no existing library image
matches the concept. Logos and brand marks, product photos and anything that could pass as
evidence or genuine footage are never generated. Every generated picture says so (source type
AI_GENERATED, tag ``ai_generated``) and carries the full request and generator identity.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from media_house.modules.visual_asset_generation.domain.values import JsonValue

GENERATED_TAG = "ai_generated"


class GenerationKind(StrEnum):
    ILLUSTRATION = "illustration"
    CONCEPT_VISUAL = "concept_visual"
    DECORATIVE_BACKGROUND = "decorative_background"
    EXPLANATORY_ELEMENT = "explanatory_element"
    # Declared so they can be refused explicitly; never generated:
    LOGO = "logo"
    PRODUCT_PHOTO = "product_photo"
    EVIDENCE = "evidence"
    REALISTIC_FOOTAGE = "realistic_footage"


_REFUSED: dict[GenerationKind, str] = {
    GenerationKind.LOGO: "logos and brand marks are never generated",
    GenerationKind.PRODUCT_PHOTO: (
        "product photos are never generated (they would misrepresent the product)"
    ),
    GenerationKind.EVIDENCE: "visual evidence is never generated",
    GenerationKind.REALISTIC_FOOTAGE: (
        "realistic scenes that could pass as genuine footage are never generated"
    ),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationRequest:
    request_id: str
    concept: str
    kind: GenerationKind
    prompt: str
    #: Why a new picture is needed (for example the b-roll coverage gap it closes).
    reason: str
    width: int = 1080
    height: int = 1080
    seed: int | None = None
    #: The b-roll ``CoverageGap.gap_id`` this would close, if any.
    gap_id: str | None = None

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "request_id": self.request_id,
            "concept": self.concept,
            "kind": self.kind.value,
            "prompt": self.prompt,
            "reason": self.reason,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "gap_id": self.gap_id,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationRecord:
    """Provenance of one generated picture (stored as its asset metadata)."""

    request: GenerationRequest
    generator: str
    model: str
    model_version: str
    seed: int | None
    created_at: datetime

    def to_metadata(self) -> dict[str, JsonValue]:
        return {
            "generated": True,
            "generation_request": self.request.to_config(),
            "generator": self.generator,
            "generation_model": self.model,
            "generation_model_version": self.model_version,
            "generation_seed": self.seed,
            "generated_at": self.created_at.isoformat(),
        }


def generation_problems(request: GenerationRequest, existing_matches: int) -> tuple[str, ...]:
    """Every reason the request must not be generated; empty = allowed."""
    problems: list[str] = []
    if request.kind in _REFUSED:
        problems.append(_REFUSED[request.kind])
    if existing_matches > 0:
        problems.append(
            f"{existing_matches} existing library image(s) match the concept: reuse them"
        )
    if not request.reason.strip():
        problems.append("the need for a new picture is not stated")
    if not request.prompt.strip() or not request.concept.strip():
        problems.append("a concept and a prompt are required")
    if not (64 <= request.width <= 4096 and 64 <= request.height <= 4096):
        problems.append("the size must be 64-4096 pixels per side")
    return tuple(problems)
