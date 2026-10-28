"""Building source ranges: exact seconds, plus the frame and sample grid when it is known."""

from fractions import Fraction

from media_house.modules.editing_intelligence.domain.facts import SourceFacts
from media_house.modules.editing_intelligence.domain.model import SourceRange
from media_house.modules.editing_intelligence.domain.timing import (
    frame_ceil,
    frame_floor,
    sample_round,
    to_rational,
)
from media_house.modules.editing_intelligence.domain.values import Precision


def make_range(
    source: SourceFacts,
    start: Fraction,
    end: Fraction,
    start_precision: Precision,
    end_precision: Precision,
) -> SourceRange:
    """``[start, end)`` of the recording. Frames exist only for a constant, known frame rate."""
    rate = source.constant_frame_rate
    samples = source.sample_rate
    return SourceRange(
        asset_id=source.asset_id,
        start=to_rational(start),
        end=to_rational(end),
        start_precision=start_precision,
        end_precision=end_precision,
        frame_rate=rate,
        start_frame=None if rate is None else frame_floor(start, rate),
        end_frame=None if rate is None else frame_ceil(end, rate),
        sample_rate=samples,
        start_sample=None if samples is None else sample_round(start, samples),
        end_sample=None if samples is None else sample_round(end, samples),
    )
