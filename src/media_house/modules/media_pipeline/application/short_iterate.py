"""The next attempt at a Short: what the quality gate's remedies change in the run's settings.

Each remedy turns one planning knob a bounded step; a knob at its bound is left alone, so a run
that cannot get better stops instead of looping. Remedies without a knob here (reframing, caption
coverage, emoji, loudness, uneven effects) are reported for a person and change nothing.
"""

from dataclasses import replace

from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.domain.short_gate import Remedy

_STEP_DB = 3.0
_MAX_BUS_DB = 9.0
_SHORT_BEAT = 2.2  # edit_plan_compiler's default for a portrait output
_BEAT_STEP = 0.4
_MIN_BEAT = 1.4
_SFX_SPACING = (1.2, 0.8)  # the Shorts spacing, then the densest still readable
_EXCERPT_STEP = 5.0
_MAX_SHORT_S = 60.0


def next_attempt(settings: StageSettings, remedies: tuple[str, ...]) -> StageSettings | None:
    """The settings of the next attempt, or ``None`` when no remedy can change anything."""
    wanted = {Remedy(r) for r in remedies if r in Remedy._value2member_map_}
    changed = settings
    buses = dict(settings.mix_bus_gain_db)

    def bus(role: str, step: float) -> None:
        level = buses.get(role, 0.0) + step
        if abs(level) <= _MAX_BUS_DB:
            buses[role] = level

    if Remedy.MUSIC_UP in wanted:
        bus("music", _STEP_DB)
    if Remedy.MUSIC_DOWN in wanted:
        bus("music", -_STEP_DB)
    if Remedy.SFX_UP in wanted:
        bus("sfx", _STEP_DB)
    if Remedy.SFX_DOWN in wanted:
        bus("sfx", -_STEP_DB)
    changed = replace(changed, mix_bus_gain_db=tuple(sorted(buses.items())))
    beat = settings.camera_beat or _SHORT_BEAT
    if Remedy.MORE_MOTION in wanted and beat > _MIN_BEAT:
        changed = replace(changed, camera_beat=round(max(_MIN_BEAT, beat - _BEAT_STEP), 2))
    if Remedy.MORE_SFX in wanted:
        overrides = dict(settings.sound_overrides)
        if overrides.get("sfx.min_spacing_seconds") != _SFX_SPACING[1]:
            overrides["sfx.min_spacing_seconds"] = _SFX_SPACING[1]
            changed = replace(changed, sound_overrides=tuple(overrides.items()))
    if settings.edit_excerpt is not None:
        low, high = settings.edit_excerpt
        if Remedy.SHORTEN in wanted and high - _EXCERPT_STEP >= low:
            changed = replace(changed, edit_excerpt=(low, high - _EXCERPT_STEP))
        elif Remedy.CONDENSE in wanted and high < _MAX_SHORT_S:
            changed = replace(changed, edit_excerpt=(low, min(_MAX_SHORT_S, high + _EXCERPT_STEP)))
    return None if changed == settings else changed
