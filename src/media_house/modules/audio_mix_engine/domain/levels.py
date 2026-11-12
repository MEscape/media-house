"""Level design: where music and effects sit against the levelled dialogue.

The dialogue is brought to the profile's dialogue target (``plan.dialogue_trims``). Music and
effects are then measured clip by clip and placed RELATIVE to that dialogue level, so the balance
never depends on how loud a library file happens to be:

* music under speech sits ``music_under_speech_lu`` below the dialogue; where nobody speaks it may
  come up to ``music_in_gaps_lu`` below (the speech duck is the difference);
* every effect is normalised by its loudest MOMENTARY loudness (400 ms windows, so a short pop is
  not judged by the silence around it) to the target of its kind: impacts, whooshes, interface
  sounds; nothing is set louder than the dialogue (``sfx_cap_lu``).

Pure: measurements in, gains out. The same numbers give the static gain of every clip for an
editing application that has no buses and no ducking (``ClipLevel.static_gain_db``).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from media_house.modules.audio_mix_engine.domain.result import ClipLevel, OutputIssue
from media_house.modules.audio_mix_engine.domain.values import Certainty, Severity

IMPACT, WHOOSH, UI, OTHER = "impact", "whoosh", "ui", "other"

#: The sound library's effect categories -> level class (a sound's first category that has one
#: wins). An unknown category is ``other``.
SFX_CLASSES: Mapping[str, str] = {
    **dict.fromkeys(("impact", "cinematic_hit", "fireworks", "jingle_stinger"), IMPACT),
    **dict.fromkeys(
        ("whoosh", "swish_swipe", "riser", "glitch", "record_scratch", "transition"), WHOOSH
    ),
    **dict.fromkeys(
        (
            "pop", "click", "ui_notification", "countdown_beeps", "correct_success",
            "fail_buzzer", "camera_shutter", "typewriter_keyboard", "paper_page_turn",
            "scribble_marker", "cash_register", "retro_game",
        ),
        UI,
    ),
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class LevelTargets:
    """LU below the levelled dialogue. Data of a mix profile, or of a delivery that needs its own
    balance (a phone speaker: Shorts)."""

    music_under_speech_lu: float = 18.0
    music_in_gaps_lu: float = 8.0
    sfx_impact_lu: float = 6.0
    sfx_whoosh_lu: float = 10.0
    sfx_ui_lu: float = 12.0
    sfx_other_lu: float = 10.0
    #: No effect is set louder than the dialogue minus this.
    sfx_cap_lu: float = 0.0
    #: A quiet file is never raised more than this (noise would come up with it).
    max_boost_db: float = 18.0
    #: Validation: music further below the dialogue than this is inaudible.
    max_music_gap_lu: float = 22.0
    #: Validation: effects of one kind may differ by this much after levelling.
    max_sfx_spread_lu: float = 3.0

    @property
    def duck_depth_db(self) -> float:
        """How far a speech duck lowers the music: from the gap level to the under-speech level."""
        return max(0.0, self.music_under_speech_lu - self.music_in_gaps_lu)

    def sfx_lu(self, level_class: str) -> float:
        return {
            IMPACT: self.sfx_impact_lu,
            WHOOSH: self.sfx_whoosh_lu,
            UI: self.sfx_ui_lu,
        }.get(level_class, self.sfx_other_lu)


def sfx_class(categories: Sequence[str]) -> str:
    return next((SFX_CLASSES[c] for c in categories if c in SFX_CLASSES), OTHER)


@dataclass(frozen=True, slots=True)
class Placement:
    """One music or effect clip as the level design sees it."""

    clip_id: str
    role: str  # music | sfx
    #: Integrated loudness (music) or loudest momentary loudness (effect) of the part it plays.
    measured_lufs: float | None
    #: The clip's gain as compiled, and a person's change on top of it (kept as it is).
    gain_db: float
    extra_db: float
    #: The profile's gain of the clip's bus (a person's bus change still acts on top).
    bus_gain_db: float
    level_class: str = OTHER
    #: Music: the depth of the speech duck acting on it in the mix (``None``: none).
    duck_db: float | None = None
    #: Music: it plays while someone speaks.
    under_speech: bool = True


def place(p: Placement, *, reference_lufs: float, targets: LevelTargets) -> ClipLevel:
    """The trim that puts a clip on its target after its bus, and the static gain an application
    without buses or ducking plays it at (its under-speech level)."""
    if p.role == "music":
        static_target = reference_lufs - targets.music_under_speech_lu
        if p.duck_db is not None:
            target = static_target + p.duck_db  # the duck brings it down under speech
        elif p.under_speech:
            target = static_target
        else:
            target = reference_lufs - targets.music_in_gaps_lu
        level_class = "music"
    else:
        level_class = p.level_class
        target = reference_lufs - max(targets.sfx_lu(level_class), targets.sfx_cap_lu)
        static_target = target
    if p.measured_lufs is None:
        return ClipLevel(
            p.clip_id, p.role, level_class, None, None, 0.0, p.gain_db + p.extra_db + p.bus_gain_db
        )
    level = min(target - p.measured_lufs, targets.max_boost_db)
    static = min(static_target - p.measured_lufs, targets.max_boost_db)
    return ClipLevel(
        clip_id=p.clip_id,
        role=p.role,
        level_class=level_class,
        measured_lufs=p.measured_lufs,
        target_lufs=round(target, 1),
        trim_db=round(level - p.bus_gain_db - p.gain_db, 1),
        static_gain_db=round(static + p.extra_db, 1),
    )


def level_issues(levels: Sequence[ClipLevel], targets: LevelTargets) -> tuple[OutputIssue, ...]:
    """Effects of one kind that still differ after levelling (a file too quiet to be raised far
    enough), and music or effects that could not be measured (played at their compiled gain)."""
    found: list[OutputIssue] = []
    by_class: dict[str, list[ClipLevel]] = {}
    for lv in levels:
        if lv.role not in {"music", "sfx"}:
            continue
        if lv.measured_lufs is None:
            found.append(
                OutputIssue(
                    "level_not_measured", Severity.INFO, Certainty.RECOMMENDATION,
                    f"{lv.clip_id} could not be measured: it plays at its compiled gain",
                    element_id=lv.clip_id,
                )
            )  # fmt: skip
        elif lv.role == "sfx":
            by_class.setdefault(lv.level_class, []).append(lv)
    for level_class, group in sorted(by_class.items()):
        heard = [
            lv.measured_lufs + lv.static_gain_db for lv in group if lv.measured_lufs is not None
        ]
        spread = max(heard, default=0.0) - min(heard, default=0.0)
        if spread > targets.max_sfx_spread_lu:
            found.append(
                OutputIssue(
                    "sfx_levels_inconsistent", Severity.WARNING, Certainty.LIKELY,
                    f"{level_class} effects differ by {spread:.1f} LU after levelling "
                    f"(at most {targets.max_sfx_spread_lu:g})",
                    evidence=", ".join(f"{lv.clip_id} {lv.static_gain_db:+.1f} dB" for lv in group),
                )
            )  # fmt: skip
    return tuple(found)
