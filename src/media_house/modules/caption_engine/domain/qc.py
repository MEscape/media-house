"""Caption QC: timing, readability, zones, text integrity (contracts.md section 6).

CRITICAL = the plan is invalid and never stored (a caption that does not say what was said, a
word outside its caption). ERROR = one caption is unusable (wrong zone, platform keep-out,
overlap): it is not rendered and the plan is PARTIAL. WARNING / INFO are recorded.
"""

from collections.abc import Sequence
from fractions import Fraction

from media_house.modules.caption_engine.domain.facts import StyleFacts, frac, rat
from media_house.modules.caption_engine.domain.model import CaptionSegment
from media_house.modules.caption_engine.domain.profile import Segmentation
from media_house.modules.caption_engine.domain.text import same_text, strip_full_stop
from media_house.modules.caption_engine.domain.values import QcCategory, QcIssue, Severity


def issue(
    code: str,
    severity: Severity,
    message: str,
    item_id: str | None = None,
    *,
    start: Fraction | None = None,
    end: Fraction | None = None,
    evidence: str = "",
    category: QcCategory = QcCategory.TECHNICAL,
) -> QcIssue:
    return QcIssue(
        code=code,
        severity=severity,
        category=category,
        message=message,
        item_id=item_id,
        timeline_start=rat(start) if start is not None else None,
        timeline_end=rat(end) if end is not None else None,
        evidence=evidence,
    )


def check_segments(
    segments: Sequence[CaptionSegment],
    rules: Segmentation,
    style: StyleFacts,
    max_chars: int,
    tolerance: Fraction,
) -> list[QcIssue]:
    found: list[QcIssue] = []
    for k, s in enumerate(segments):
        sid, start, end = s.segment_id, frac(s.timeline_start), frac(s.timeline_end)
        length = end - start
        if s.end_frame <= s.start_frame:
            found.append(issue("timing.no_frames", Severity.ERROR, "shorter than one frame", sid))
        if k + 1 < len(segments) and frac(segments[k + 1].timeline_start) < end:
            found.append(
                issue(
                    "timing.overlap",
                    Severity.ERROR,
                    f"overlaps {segments[k + 1].segment_id}",
                    sid,
                    start=start,
                    end=end,
                )
            )
        if float(length) + 1e-9 < rules.min_duration:
            found.append(
                issue(
                    "timing.too_short",
                    Severity.WARNING,
                    f"shown {float(length):.2f} s (shortest {rules.min_duration:.2f} s): the"
                    " next caption follows too closely",
                    sid,
                    start=start,
                    end=end,
                )
            )
        if float(length) > rules.max_duration + 1e-9:
            found.append(
                issue(
                    "timing.too_long",
                    Severity.WARNING,
                    f"shown {float(length):.2f} s (longest {rules.max_duration:.2f} s)",
                    sid,
                    start=start,
                    end=end,
                )
            )
        chars = sum(len(line) for line in s.layout.lines)
        if length > 0 and chars / float(length) > rules.reading_cps + 1e-9:
            found.append(
                issue(
                    "reading.speed",
                    Severity.WARNING,
                    f"{chars / float(length):.1f} chars/s (limit {rules.reading_cps:.0f}):"
                    " fast speech, the caption follows it",
                    sid,
                    start=start,
                    end=end,
                )
            )
        for line in s.layout.lines:
            if len(line) > max_chars:
                found.append(
                    issue(
                        "line.too_long",
                        Severity.WARNING,
                        f"{len(line)} characters (limit {max_chars}); motion graphics shrinks"
                        " or refuses it",
                        sid,
                        evidence=line,
                    )
                )
        if len(s.layout.lines) > style.max_lines:
            found.append(
                issue("line.too_many", Severity.WARNING, f"{len(s.layout.lines)} lines", sid)
            )
        if s.layout.size_px < style.min_text_px:
            found.append(
                issue(
                    "readability.size",
                    Severity.WARNING,
                    f"{s.layout.size_px} px text at {style.width}x{style.height} is below the"
                    f" style's {style.min_text_px} px floor",
                    sid,
                )
            )
        found += _words(s, start, end, tolerance)
    return found


def _words(s: CaptionSegment, start: Fraction, end: Fraction, tolerance: Fraction) -> list[QcIssue]:
    found: list[QcIssue] = []
    if not s.words:
        found.append(
            issue("text.empty", Severity.CRITICAL, "a caption without words", s.segment_id)
        )
    for w in s.words:
        if not w.display.strip() or not (
            same_text(w.display, w.text) or same_text(w.display, strip_full_stop(w.text))
        ):
            found.append(
                issue(
                    "text.integrity",
                    Severity.CRITICAL,
                    f"{w.display!r} is not what was said ({w.text!r})",
                    s.segment_id,
                )
            )
        local_start, local_end = frac(w.local_start), frac(w.local_end)
        if not Fraction(0) <= local_start < local_end <= end - start:
            found.append(
                issue(
                    "timing.word_outside",
                    Severity.CRITICAL,
                    f"word {w.word_id} is not inside its caption",
                    s.segment_id,
                )
            )
        drift = abs(frac(w.timeline_start) - (start + local_start))
        if drift > tolerance:
            found.append(
                issue(
                    "timing.drift",
                    Severity.ERROR,
                    f"word {w.word_id} drifts {float(drift):.3f} s from its caption timing",
                    s.segment_id,
                )
            )
    return found
