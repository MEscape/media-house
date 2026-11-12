"""The quality gate of a delivered Short: is it watchable to the end?

A Short is judged on what a viewer meets in the delivered file, measured, not on what the plans
intended: the picture fills the 9:16 frame, something visibly changes every few seconds, the voice
sits clearly above the music and every sound effect lands at a consistent level, the captions carry
the speech (with emoji), and the story is complete (the hook, every item, the payoff). Each failed
rule names a ``remedy``: the planning knob a next iteration turns. Pure: the numbers come in, the
verdict goes out.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise


class Remedy(StrEnum):
    """What the next attempt changes when a rule fails."""

    REFRAME = "reframe"  # the picture does not fill the frame
    MORE_MOTION = "more_motion"  # denser punch-ins, inserts and transitions
    MUSIC_UP = "music_up"
    MUSIC_DOWN = "music_down"
    SFX_UP = "sfx_up"  # effects too far under the voice
    SFX_DOWN = "sfx_down"  # an effect louder than the voice
    SFX_LEVEL = "sfx_level"  # effects uneven among themselves (the engine's levelling)
    MORE_SFX = "more_sfx"
    MORE_CAPTIONS = "more_captions"
    MORE_EMOJI = "more_emoji"
    CONDENSE = "condense"  # the story misses parts or is too short: more room
    SHORTEN = "shorten"  # the Short overruns
    LOUDNESS = "loudness"


@dataclass(frozen=True, slots=True)
class ShortBar:
    """The thresholds of a Short (one place to tune them)."""

    longest_still_s: float = 3.0
    #: Two pictures this similar (1.0 = identical) show no visible change. Measured on real
    #: Shorts: a talking head with only its captions changing is >= 0.96, a card's beat or entry
    #: about 0.94, a punch-in or cut <= 0.90.
    change_similarity: float = 0.95
    letterbox_fraction: float = 0.04
    music_gap_lu: tuple[float, float] = (12.0, 22.0)
    sfx_below_dialogue_lu: tuple[float, float] = (0.0, 16.0)
    sfx_spread_lu: float = 8.0
    sfx_per_minute: float = 10.0
    caption_coverage: float = 0.85
    emoji_per_minute: float = 8.0
    duration_s: tuple[float, float] = (30.0, 60.0)
    integrated_lufs: tuple[float, float] = (-15.0, -13.0)
    max_true_peak_dbtp: float = -1.0


@dataclass(frozen=True, slots=True)
class ShortFacts:
    """What was measured on (and around) one delivered Short."""

    duration_s: float
    #: (time, similarity to the previous sample), sampled evenly through the video.
    changes: tuple[tuple[float, float], ...]
    #: Per sampled frame: the larger near-black border of top and bottom (fraction of the height).
    letterbox: tuple[float, ...]
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    #: Loudness of the dialogue and of the music while someone speaks (LUFS).
    dialogue_lufs: float | None
    music_under_speech_lufs: float | None
    #: Loudness of each sound effect over its own span (LUFS).
    sfx_lufs: tuple[float, ...]
    speech_s: float
    captioned_s: float
    emoji: int
    #: Story parts the edit must carry (hook, every item, payoff) and those it does.
    required_parts: tuple[str, ...]
    present_parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateFinding:
    code: str
    measured: str
    limit: str
    remedy: Remedy


@dataclass(frozen=True, slots=True)
class ShortVerdict:
    findings: tuple[GateFinding, ...]
    #: Every number the verdict rests on (for the report).
    measured: tuple[tuple[str, str], ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def remedies(self) -> tuple[Remedy, ...]:
        return tuple(dict.fromkeys(f.remedy for f in self.findings))


def longest_still(changes: Sequence[tuple[float, float]], duration: float, similar: float) -> float:
    """The longest stretch between two visible changes (start and end count as changes)."""
    marks = [0.0, *(t for t, s in changes if s < similar), duration]
    return max((b - a for a, b in pairwise(marks)), default=duration)


def judge_short(facts: ShortFacts, bar: ShortBar | None = None) -> ShortVerdict:
    bar = bar or ShortBar()
    found: list[GateFinding] = []
    measured: list[tuple[str, str]] = []
    minutes = max(facts.duration_s, 1.0) / 60.0
    # emoji ride on captions, and captions pause under full-screen cards
    talk = max(facts.captioned_s or facts.duration_s, 1.0) / 60.0

    def rule(code: str, value: float, ok: bool, limit: str, remedy: Remedy) -> None:
        measured.append((code, f"{value:.2f}"))
        if not ok:
            found.append(GateFinding(code, f"{value:.2f}", limit, remedy))

    lo, hi = bar.duration_s
    rule(
        "duration_s",
        facts.duration_s,
        lo <= facts.duration_s <= hi,
        f"{lo}-{hi}",
        Remedy.SHORTEN if facts.duration_s > hi else Remedy.CONDENSE,
    )
    missing = [p for p in facts.required_parts if p not in set(facts.present_parts)]
    rule("story_missing", len(missing), not missing, "0", Remedy.CONDENSE)
    if facts.letterbox:
        boxed = sum(b > bar.letterbox_fraction for b in facts.letterbox) / len(facts.letterbox)
        rule("letterboxed_frames", boxed, boxed == 0.0, "0", Remedy.REFRAME)
    if facts.changes:
        still = longest_still(facts.changes, facts.duration_s, bar.change_similarity)
        rule(
            "longest_still_s",
            still,
            still <= bar.longest_still_s,
            f"<={bar.longest_still_s}",
            Remedy.MORE_MOTION,
        )
    if facts.dialogue_lufs is not None and facts.music_under_speech_lufs is not None:
        gap = facts.dialogue_lufs - facts.music_under_speech_lufs
        g_lo, g_hi = bar.music_gap_lu
        remedy = Remedy.MUSIC_UP if gap > g_hi else Remedy.MUSIC_DOWN
        rule("music_gap_lu", gap, g_lo <= gap <= g_hi, f"{g_lo}-{g_hi}", remedy)
    rule(
        "sfx_per_minute",
        len(facts.sfx_lufs) / minutes,
        len(facts.sfx_lufs) / minutes >= bar.sfx_per_minute,
        f">={bar.sfx_per_minute}",
        Remedy.MORE_SFX,
    )
    if facts.sfx_lufs and facts.dialogue_lufs is not None:
        below = [facts.dialogue_lufs - s for s in facts.sfx_lufs]
        b_lo, b_hi = bar.sfx_below_dialogue_lu
        worst = max(below, key=lambda b: max(b_lo - b, b - b_hi))
        rule(
            "sfx_below_dialogue_lu",
            worst,
            b_lo <= worst <= b_hi,
            f"{b_lo}-{b_hi}",
            Remedy.SFX_UP if worst > b_hi else Remedy.SFX_DOWN,
        )
        spread = max(below) - min(below)
        rule(
            "sfx_spread_lu",
            spread,
            spread <= bar.sfx_spread_lu,
            f"<={bar.sfx_spread_lu}",
            Remedy.SFX_LEVEL,
        )
    if facts.speech_s:
        cover = facts.captioned_s / facts.speech_s
        rule(
            "caption_coverage",
            cover,
            cover >= bar.caption_coverage,
            f">={bar.caption_coverage}",
            Remedy.MORE_CAPTIONS,
        )
    rule(
        "emoji_per_minute",
        facts.emoji / talk,
        facts.emoji / talk >= bar.emoji_per_minute,
        f">={bar.emoji_per_minute}",
        Remedy.MORE_EMOJI,
    )
    if facts.integrated_lufs is not None:
        i_lo, i_hi = bar.integrated_lufs
        rule(
            "integrated_lufs",
            facts.integrated_lufs,
            i_lo <= facts.integrated_lufs <= i_hi,
            f"{i_lo}-{i_hi}",
            Remedy.LOUDNESS,
        )
    if facts.true_peak_dbtp is not None:
        rule(
            "true_peak_dbtp",
            facts.true_peak_dbtp,
            facts.true_peak_dbtp <= bar.max_true_peak_dbtp,
            f"<={bar.max_true_peak_dbtp}",
            Remedy.LOUDNESS,
        )
    return ShortVerdict(tuple(found), tuple(measured))
