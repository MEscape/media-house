"""SubRip (SRT) and WebVTT text from subtitle cues (pure; the plan stores the text).

Timestamps are the exact cue times rounded to milliseconds (half to even); a cue never ends
before it starts after rounding. WebVTT escapes ``&``, ``<`` and ``>`` (so ``-->`` cannot
appear in a cue); SRT text is written as is. Lines use ``\\n``; YouTube accepts both files.
"""

from collections.abc import Sequence

from media_house.modules.caption_engine.domain.facts import frac
from media_house.modules.caption_engine.domain.model import SubtitleCue


def _ms(cue: SubtitleCue) -> tuple[int, int]:
    start = round(frac(cue.timeline_start) * 1000)
    end = max(round(frac(cue.timeline_end) * 1000), start + 1)
    return start, end


def _stamp(ms: int, separator: str) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def to_srt(cues: Sequence[SubtitleCue]) -> str:
    blocks = []
    for number, cue in enumerate(cues, start=1):
        start, end = _ms(cue)
        text = "\n".join(cue.lines)
        blocks.append(f"{number}\n{_stamp(start, ',')} --> {_stamp(end, ',')}\n{text}\n")
    return "\n".join(blocks)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_vtt(cues: Sequence[SubtitleCue], *, speaker_labels: bool = False) -> str:
    blocks = ["WEBVTT\n"]
    for cue in cues:
        start, end = _ms(cue)
        lines = [_escape(line) for line in cue.lines]
        if speaker_labels and cue.speaker and lines:
            lines[0] = f"<v {_escape(cue.speaker)}>{lines[0]}"
        blocks.append(f"{_stamp(start, '.')} --> {_stamp(end, '.')}\n" + "\n".join(lines) + "\n")
    return "\n".join(blocks)
