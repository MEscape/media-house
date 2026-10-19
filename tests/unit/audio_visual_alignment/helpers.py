"""Shared helpers for the alignment unit tests."""

from datetime import UTC, datetime

from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.engine import align
from media_house.modules.audio_visual_alignment.domain.inputs import Inputs
from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def run(inputs: Inputs, profile: AlignmentProfile | None = None) -> AlignmentDocument:
    return align(
        inputs,
        profile or AlignmentProfile(),
        alignment_id="av_test",
        created_at=NOW,
        records=(),
        engines={},
    )
