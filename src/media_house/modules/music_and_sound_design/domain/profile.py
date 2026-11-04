"""Sound identity and format profiles: every creative number of this module lives here.

Precedence (each step touches only the keys it names): system defaults -> the channel's sound
identity (``CHANNEL``) -> a format profile (``PROFILES``) -> project overrides (dotted paths,
``SoundDesignConfig.overrides``) -> section overrides -> per-item person decisions (in the
command). The sound identity is the channel's own, versioned, and independent of the visual
identity: no audio setting is derived from colours or fonts.

Every user-facing knob carries ``dataclasses.field`` metadata (``label``, ``help``, ``unit``,
``min``, ``max``, ``step``, ``choices``, ``kind``, ``advanced``) for a future settings UI;
``problems()`` enforces exactly those ranges and choices (it reads the same metadata), so the
description and the check cannot drift.

The format profiles come from the channel discovery (MEsc, a German fireworks channel). The
audio facts there were measured (loudness, silence) but the music/SFX habits are assumptions;
the values are a starting point for the owner, not a measurement.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any, Final

from media_house.modules.music_and_sound_design.domain.errors import InvalidSoundConfig
from media_house.modules.music_and_sound_design.domain.library import LicencePolicy
from media_house.modules.music_and_sound_design.domain.values import (
    PROFILE_VERSION,
    EventSource,
    MusicPresence,
    Need,
    SectionRole,
    SfxAlign,
)

type OverrideValue = str | int | float | bool | list[str] | tuple[str, ...] | None

_PRESENCES: Final = tuple(p.value for p in MusicPresence)
_ROLES: Final = tuple(r.value for r in SectionRole)
_UNDER_SPEECH: Final = ("never", "soft_only", "allow")
_OVER_PRESERVED: Final = ("off", "duck")


def knob(
    default: Any,
    label: str,
    help: str = "",  # noqa: A002 - the metadata key of the convention
    *,
    unit: str | None = None,
    min: float | None = None,  # noqa: A002
    max: float | None = None,  # noqa: A002
    step: float | None = None,
    choices: tuple[str, ...] | None = None,
    kind: str | None = None,
    advanced: bool = False,
) -> Any:
    meta: dict[str, object] = {"label": label, "help": help, "advanced": advanced}
    for key, value in (
        ("unit", unit),
        ("min", min),
        ("max", max),
        ("step", step),
        ("choices", choices),
        ("kind", kind),
    ):
        if value is not None:
            meta[key] = value
    return field(default=default, metadata=meta)


def _presence(default: MusicPresence, role: str) -> Any:
    return knob(
        default.value,
        f"Music in {role}",
        "feature = music in front, bed = under speech, off = no music, silence = deliberate"
        " silence",
        choices=_PRESENCES,
    )


def _energy(default: float, role: str) -> Any:
    return knob(
        default,
        f"Energy in {role}",
        "Wanted musical energy (0 calm .. 1 intense), matched against MEASURED tempo,"
        " onset rate and level",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
    )


def _categories(default: tuple[str, ...], role: str) -> Any:
    return knob(
        default,
        f"Music categories for {role}",
        "Library categories to choose from, best first (the owner's manifest vocabulary)",
        kind="categories",
    )


@dataclass(frozen=True, slots=True)
class RolePresence:
    hook: str = _presence(MusicPresence.FEATURE, "the hook")
    intro: str = _presence(MusicPresence.BED, "the intro")
    body: str = _presence(MusicPresence.BED, "the body")
    explanation: str = _presence(MusicPresence.BED, "explanations")
    demonstration: str = _presence(MusicPresence.OFF, "demonstrations and tests")
    reveal: str = _presence(MusicPresence.FEATURE, "reveals")
    recap: str = _presence(MusicPresence.BED, "the recap")
    call_to_action: str = _presence(MusicPresence.BED, "the call to action")
    outro: str = _presence(MusicPresence.FEATURE, "the outro")

    def of(self, role: SectionRole) -> MusicPresence:
        return MusicPresence(str(getattr(self, role.value)))


@dataclass(frozen=True, slots=True)
class RoleEnergy:
    hook: float = _energy(0.75, "the hook")
    intro: float = _energy(0.5, "the intro")
    body: float = _energy(0.5, "the body")
    explanation: float = _energy(0.35, "explanations")
    demonstration: float = _energy(0.5, "demonstrations")
    reveal: float = _energy(0.85, "reveals")
    recap: float = _energy(0.5, "the recap")
    call_to_action: float = _energy(0.55, "the call to action")
    outro: float = _energy(0.65, "the outro")

    def of(self, role: SectionRole) -> float:
        return float(getattr(self, role.value))


_UPBEAT: Final = ("upbeat_positive", "hype_energetic")


@dataclass(frozen=True, slots=True)
class RoleCategories:
    hook: tuple[str, ...] = _categories(("hype_energetic", "upbeat_positive"), "the hook")
    intro: tuple[str, ...] = _categories(_UPBEAT, "the intro")
    body: tuple[str, ...] = _categories(("upbeat_positive", "chill_lofi"), "the body")
    explanation: tuple[str, ...] = _categories(("chill_lofi", "upbeat_positive"), "explanations")
    demonstration: tuple[str, ...] = _categories(("chill_lofi",), "demonstrations")
    reveal: tuple[str, ...] = _categories(("hype_energetic", "cinematic_epic"), "reveals")
    recap: tuple[str, ...] = _categories(("upbeat_positive",), "the recap")
    call_to_action: tuple[str, ...] = _categories(_UPBEAT, "the call to action")
    outro: tuple[str, ...] = _categories(("upbeat_positive", "festive_new_year"), "the outro")

    def of(self, role: SectionRole) -> tuple[str, ...]:
        value: tuple[str, ...] = getattr(self, role.value)
        return value


@dataclass(frozen=True, slots=True)
class MusicRules:
    presence: RolePresence = field(default_factory=RolePresence)
    energy: RoleEnergy = field(default_factory=RoleEnergy)
    categories: RoleCategories = field(default_factory=RoleCategories)
    no_section_role: str = knob(
        SectionRole.BODY.value,
        "Role without sections",
        "The role of the whole video when the timeline has no sections (no-speech path)",
        choices=_ROLES,
    )
    tempo_min_bpm: float = knob(
        70.0, "Slowest tempo", "Preferred tempo range (soft)", unit="bpm", min=30, max=250, step=1
    )
    tempo_max_bpm: float = knob(
        180.0, "Fastest tempo", "Preferred tempo range (soft)", unit="bpm", min=30, max=250, step=1
    )
    min_cue_seconds: float = knob(
        4.0,
        "Shortest music cue",
        "Music shorter than this is not started (it would only flicker in)",
        unit="s",
        min=0.0,
        max=60.0,
        step=0.5,
    )
    max_tracks: int = knob(
        4, "Most tracks per video", "Fewer changes sound more consistent", min=1, max=20, step=1
    )
    change_on_role_change: bool = knob(
        True, "Change music with the section role", "A new role may get a new track"
    )
    fade_in_seconds: float = knob(
        0.5, "Fade in", unit="s", min=0.0, max=10.0, step=0.1, kind="seconds"
    )
    fade_out_seconds: float = knob(
        2.0, "Fade out", unit="s", min=0.0, max=15.0, step=0.1, kind="seconds"
    )
    crossfade_seconds: float = knob(
        1.0,
        "Crossfade",
        "The outgoing track's tail under the incoming one",
        unit="s",
        min=0.0,
        max=10.0,
        step=0.1,
        kind="seconds",
    )
    natural_ending: bool = knob(
        True,
        "Use the track's own ending",
        "Back-time the last cue so the track ends naturally instead of fading",
    )
    beat_align: bool = knob(True, "Start cues on a downbeat")
    reuse_penalty: float = knob(
        0.3,
        "Avoid recently used tracks",
        "Score penalty for a track used by the previous plans given in the command",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
        advanced=True,
    )
    min_tempo_confidence: float = knob(
        0.15,
        "Trust beats above",
        "Below this tempo confidence a track is not beat-aligned",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
        advanced=True,
    )


@dataclass(frozen=True, slots=True)
class MixIntent:
    """Levels and ducking as INTENT. audio_improvement does the dynamics and the mastering."""

    speech_reference_lufs: float = knob(
        -16.0,
        "Speech reference",
        "Assumed speech loudness the music and SFX levels are planned against",
        unit="LUFS",
        min=-30.0,
        max=-8.0,
        step=0.5,
        advanced=True,
    )
    bed_below_speech_lu: float = knob(
        18.0,
        "Music bed below speech",
        "How far music sits below speech while someone talks",
        unit="LU",
        min=6.0,
        max=40.0,
        step=0.5,
    )
    duck_depth_db: float = knob(
        8.0,
        "Ducking depth",
        "How much louder music is between speech than under it",
        unit="dB",
        min=0.0,
        max=30.0,
        step=0.5,
    )
    duck_attack_seconds: float = knob(
        0.2, "Duck attack", unit="s", min=0.01, max=2.0, step=0.01, kind="seconds"
    )
    duck_release_seconds: float = knob(
        0.6, "Duck release", unit="s", min=0.05, max=5.0, step=0.05, kind="seconds"
    )
    speech_merge_gap_seconds: float = knob(
        1.2,
        "Keep ducked through pauses shorter than",
        "Avoids pumping between words",
        unit="s",
        min=0.0,
        max=5.0,
        step=0.1,
    )
    sfx_below_speech_lu: float = knob(
        6.0,
        "Effects below speech",
        "Level of a full-strength effect relative to speech",
        unit="LU",
        min=0.0,
        max=30.0,
        step=0.5,
    )
    sfx_strength_range_db: float = knob(
        6.0,
        "Effect level range",
        "A weak event's effect is this much quieter than a full-strength one",
        unit="dB",
        min=0.0,
        max=20.0,
        step=0.5,
    )
    max_boost_db: float = knob(
        12.0,
        "Largest boost",
        "A quiet file is never raised more than this",
        unit="dB",
        min=0.0,
        max=24.0,
        step=0.5,
        advanced=True,
    )
    feature_lift_db: float = knob(
        4.0,
        "Featured music lift",
        "Music in front (no speech planned) plays this much louder than a bed between words",
        unit="dB",
        min=0.0,
        max=12.0,
        step=0.5,
    )
    headroom_dbfs: float = knob(
        -1.0,
        "Peak warning above",
        "Predicted stem peaks above this are reported as a clipping risk",
        unit="dBFS",
        min=-12.0,
        max=0.0,
        step=0.5,
        advanced=True,
    )


@dataclass(frozen=True, slots=True)
class SfxRule:
    """Which events may get which kind of effect, and how it is laid against the event."""

    source: str = knob(
        EventSource.GRAPHICS.value, "Event source", choices=tuple(e.value for e in EventSource)
    )
    kind: str = knob("", "Event kind", "The owner's event kind, e.g. rank_reveal, zoom, emoji")
    purpose: str = knob("", "Purpose", "Why the sound is there (recorded in the plan)")
    categories: tuple[str, ...] = knob((), "Effect categories", kind="categories")
    min_strength: float = knob(
        0.6,
        "Only events at least this strong",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
    )
    align: str = knob(SfxAlign.PEAK.value, "Lands on", choices=tuple(a.value for a in SfxAlign))
    offset_seconds: float = knob(
        0.0, "Offset", unit="s", min=-2.0, max=2.0, step=0.01, kind="seconds"
    )
    need: str = knob(
        Need.OPTIONAL.value,
        "When the library has none",
        "Priority of the missing-asset recommendation",
        choices=tuple(n.value for n in Need),
    )
    max_seconds: float = knob(3.0, "Longest effect", unit="s", min=0.05, max=30.0, step=0.05)
    soft: bool = knob(False, "Soft (may sit under speech)")
    gain_db: float = knob(0.0, "Extra gain", unit="dB", min=-24.0, max=12.0, step=0.5)


def _rule(
    source: EventSource,
    kind: str,
    purpose: str,
    categories: tuple[str, ...],
    *,
    min_strength: float = 0.6,
    align: SfxAlign = SfxAlign.PEAK,
    need: Need = Need.OPTIONAL,
    max_seconds: float = 3.0,
    soft: bool = False,
    gain_db: float = 0.0,
) -> SfxRule:
    return SfxRule(
        source=source.value,
        kind=kind,
        purpose=purpose,
        categories=categories,
        min_strength=min_strength,
        align=align.value,
        need=need.value,
        max_seconds=max_seconds,
        soft=soft,
        gain_db=gain_db,
    )


#: Restraint by default: no rule for plain cuts or dissolves, high thresholds for captions.
_G, _T, _C, _E = EventSource.GRAPHICS, EventSource.TRANSITION, EventSource.CAPTION, EventSource.EDIT
DEFAULT_RULES: Final = (
    _rule(_G, "rank_reveal", "rank reveal accent", ("impact", "cinematic_hit"),
          need=Need.RECOMMENDED),
    _rule(_G, "price_reveal", "price accent", ("cash_register", "pop"), min_strength=0.7,
          soft=True),
    _rule(_G, "pop_in", "graphic pops in", ("pop",), min_strength=0.8, soft=True, gain_db=-3.0),
    _rule(_G, "fly_in", "graphic flies in", ("swish_swipe", "whoosh"), min_strength=0.8, soft=True),
    _rule(_G, "drop", "object lands", ("impact", "pop"), min_strength=0.7),
    _rule(_G, "counter", "number counts", ("click", "countdown_beeps"), min_strength=0.85,
          soft=True),
    _rule(_G, "reveal", "product reveal", ("riser",), min_strength=0.7, align=SfxAlign.END,
          need=Need.RECOMMENDED, max_seconds=6.0),
    _rule(_G, "highlight", "highlight accent", ("ui_notification", "click"), min_strength=0.85,
          soft=True, gain_db=-3.0),
    _rule(_G, "logo_sting", "channel sting", ("jingle_stinger",), min_strength=0.5,
          align=SfxAlign.START, need=Need.RECOMMENDED, max_seconds=8.0),
    # transition kinds exactly as transition_engine publishes them (``TransitionKind`` values)
    _rule(_T, "zoom", "zoom transition", ("whoosh", "swish_swipe"), min_strength=0.6),
    _rule(_T, "whip_pan", "whip transition", ("swish_swipe", "whoosh"), min_strength=0.6),
    _rule(_T, "slide", "slide transition", ("swish_swipe",), min_strength=0.7, soft=True),
    _rule(_T, "push", "push transition", ("swish_swipe",), min_strength=0.7, soft=True),
    _rule(_T, "wipe", "wipe transition", ("swish_swipe",), min_strength=0.7, soft=True),
    _rule(_T, "flash", "flash transition", ("cinematic_hit", "impact"), min_strength=0.8),
    _rule(_T, "glitch", "glitch transition", ("glitch",), min_strength=0.6),
    _rule(_T, "zoom_tunnel", "zoom tunnel into the membership plug", ("whoosh",),
          min_strength=0.5),
    _rule(_T, "pixel_dissolve", "pixel dissolve into the end screen", ("swish_swipe", "whoosh"),
          min_strength=0.5, soft=True, gain_db=-3.0),
    _rule(_C, "emoji", "emoji pop", ("pop",), min_strength=0.85, soft=True, gain_db=-4.0),
    _rule(_C, "keyword", "key word emphasis", ("pop", "click"), min_strength=0.9, soft=True,
          gain_db=-6.0),
    _rule(_E, "section_change", "section change", ("swish_swipe", "whoosh"), min_strength=0.75),
    _rule(_E, "reveal", "editorial reveal", ("riser",), min_strength=0.7, align=SfxAlign.END,
          max_seconds=6.0),
)  # fmt: skip


#: transition_engine kinds that deliberately get NO effect (restraint: cuts, blends and fades
#: are heard through the picture, not an added sound). Every other kind has a rule above.
SILENT_TRANSITIONS: Final = frozenset(
    {
        "hard_cut",
        "match_cut",
        "action_cut",
        "jump_cut",
        "cross_dissolve",
        "fade_in",
        "fade_out",
        "dip_to_black",
        "dip_to_white",
        "motion_match",
        "blur",
        "shape",
        "mask_reveal",
    }
)


@dataclass(frozen=True, slots=True)
class SfxRules:
    enabled: bool = knob(True, "Sound effects")
    max_per_minute: float = knob(
        6.0, "Most effects per minute", unit="per min", min=0.0, max=60.0, step=0.5
    )
    min_spacing_seconds: float = knob(
        1.5, "Least time between effects", unit="s", min=0.0, max=30.0, step=0.1
    )
    under_speech: str = knob(
        "soft_only",
        "Effects under speech",
        "never, only soft ones (pops, clicks), or any",
        choices=_UNDER_SPEECH,
    )
    repeat_window: int = knob(
        3,
        "Do not repeat a sound within",
        "Number of previous effects that may not use the same file",
        min=0,
        max=20,
        step=1,
    )
    rules: tuple[SfxRule, ...] = knob(DEFAULT_RULES, "Event rules", kind="rules")

    def rule_for(self, source: EventSource, kind: str) -> SfxRule | None:
        return next((r for r in self.rules if r.source == source.value and r.kind == kind), None)


@dataclass(frozen=True, slots=True)
class SilenceRules:
    reveal_dropout_seconds: float = knob(
        0.0,
        "Music drop before a reveal",
        "Music stops this long before a held reveal (0 = never)",
        unit="s",
        min=0.0,
        max=5.0,
        step=0.1,
    )
    min_music_gap_seconds: float = knob(
        3.0,
        "Shortest music pause",
        "A music-free gap shorter than this is bridged with the bed (unless the audio is"
        " preserved)",
        unit="s",
        min=0.0,
        max=30.0,
        step=0.5,
    )
    max_dead_air_seconds: float = knob(
        2.0,
        "Longest stretch without any sound",
        "Longer stretches without speech, music or clip sound are reported",
        unit="s",
        min=0.0,
        max=30.0,
        step=0.5,
    )


@dataclass(frozen=True, slots=True)
class OriginalAudioRules:
    preserve_fireworks: bool = knob(
        True,
        "Preserve fireworks audio",
        "Original bangs and crackle are the content: no music over them by default",
    )
    content_roles: tuple[str, ...] = knob(
        (SectionRole.DEMONSTRATION.value,),
        "Roles whose clip sound is the content",
        kind="roles",
    )
    source_audio_is_content: bool = knob(
        False,
        "Clip sound is the content",
        "Every clip without speech keeps its own sound in front (compilations, uncut footage)",
    )
    incidental_speech_share: float = knob(
        0.1,
        "Incidental speech below",
        "Speech covering less of a clip than this share (shouts over fireworks) does not make it"
        " a speech clip: its own sound stays the content",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
    )
    event_strength: float = knob(
        0.6,
        "Loud original moments from",
        "Original-audio events at least this strong are protected",
        unit="fraction",
        min=0.0,
        max=1.0,
        step=0.05,
    )
    music_over_preserved: str = knob(
        "off",
        "Music over preserved sound",
        "off = no music, duck = music ducked under it",
        choices=_OVER_PRESERVED,
    )
    duck_db: float = knob(
        10.0,
        "Duck under preserved sound",
        unit="dB",
        min=0.0,
        max=40.0,
        step=0.5,
    )


CHANNEL_IDENTITY: Final = "mesc_channel"
CHANNEL_IDENTITY_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class SoundProfile:
    name: str
    description: str
    version: int = 1
    identity: str = CHANNEL_IDENTITY
    identity_version: int = CHANNEL_IDENTITY_VERSION
    music: MusicRules = field(default_factory=MusicRules)
    mix: MixIntent = field(default_factory=MixIntent)
    sfx: SfxRules = field(default_factory=SfxRules)
    silence: SilenceRules = field(default_factory=SilenceRules)
    original_audio: OriginalAudioRules = field(default_factory=OriginalAudioRules)

    def problems(self) -> list[str]:
        found = _metadata_problems(self, "")
        if self.music.tempo_min_bpm >= self.music.tempo_max_bpm:
            found.append("music.tempo_min_bpm is not below music.tempo_max_bpm")
        for role in SectionRole:
            if not all(c.strip() for c in self.music.categories.of(role)):
                found.append(f"music.categories.{role.value}: empty category name")
        for i, rule in enumerate(self.sfx.rules):
            if not rule.kind.strip() or not rule.categories:
                found.append(f"sfx.rules[{i}]: needs a kind and at least one category")
        for role_name in self.original_audio.content_roles:
            if role_name not in _ROLES:
                found.append(f"original_audio.content_roles: unknown role {role_name!r}")
        return found


def _metadata_problems(target: object, prefix: str) -> list[str]:
    found: list[str] = []
    for f in fields(target):  # type: ignore[arg-type]
        value = getattr(target, f.name)
        path = f"{prefix}{f.name}"
        if is_dataclass(value):
            found += _metadata_problems(value, path + ".")
            continue
        if isinstance(value, tuple) and value and is_dataclass(value[0]):
            for i, item in enumerate(value):
                found += _metadata_problems(item, f"{path}[{i}].")
            continue
        meta = f.metadata
        low, high, choices = meta.get("min"), meta.get("max"), meta.get("choices")
        if isinstance(value, int | float) and not isinstance(value, bool):
            if low is not None and value < low:
                found.append(f"{path} must be at least {low}")
            if high is not None and value > high:
                found.append(f"{path} must be at most {high}")
        if choices is not None and value not in choices:
            found.append(f"{path} must be one of {', '.join(choices)}")
    return found


# --- built-in profiles ---------------------------------------------------------------------
CHANNEL: Final = SoundProfile(
    name="generic",
    description="The channel's sound identity: a clear voice over a quiet, upbeat bed, few"
    " effects, and fireworks heard as they are.",
)


def _format(name: str, description: str, **groups: object) -> SoundProfile:
    return replace(CHANNEL, name=name, description=description, **groups)  # type: ignore[arg-type]


_BASE = CHANNEL
_OFF = MusicPresence.OFF.value
_BED = MusicPresence.BED.value
_FEATURE = MusicPresence.FEATURE.value

PROFILES: Final[dict[str, SoundProfile]] = {
    p.name: p
    for p in (
        CHANNEL,
        _format(
            "ranking",
            "Top-N countdown: a continuous bed under the voice-over, a restrained accent on each"
            " rank reveal, test footage with its own sound.",
            music=replace(
                _BASE.music,
                categories=replace(
                    _BASE.music.categories,
                    hook=("hype_energetic", "suspense_build"),
                    reveal=("hype_energetic", "cinematic_epic"),
                ),
            ),
            silence=replace(_BASE.silence, reveal_dropout_seconds=0.5),
        ),
        _format(
            "brochure_review",
            "Catalogue review: speech-dominated (about 170 wpm), so the bed sits lower and"
            " effects are rare (page turns).",
            music=replace(
                _BASE.music,
                presence=replace(_BASE.music.presence, hook=_BED, reveal=_BED),
                categories=replace(_BASE.music.categories, body=("chill_lofi", "upbeat_positive")),
            ),
            mix=replace(_BASE.mix, bed_below_speech_lu=20.0),
            sfx=replace(
                _BASE.sfx,
                max_per_minute=4.0,
                rules=(
                    *DEFAULT_RULES,
                    _rule(
                        _G,
                        "page_turn",
                        "page turn",
                        ("paper_page_turn",),
                        min_strength=0.6,
                        soft=True,
                    ),
                ),
            ),
        ),
        _format(
            "shopping_tour",
            "Shopping tour: crowd and laughter are kept; a bed on travel scenes; a whoosh on the"
            " zoom transitions.",
            music=replace(
                _BASE.music,
                categories=replace(_BASE.music.categories, body=("chill_lofi", "upbeat_positive")),
            ),
            original_audio=replace(_BASE.original_audio, music_over_preserved="duck"),
        ),
        _format(
            "unboxing",
            "Unboxing: the real sound of opening the boxes matters more than added effects;"
            " minimal background music.",
            music=replace(
                _BASE.music,
                presence=replace(_BASE.music.presence, body=_BED, explanation=_OFF),
                categories=replace(_BASE.music.categories, body=("chill_lofi",)),
            ),
            mix=replace(_BASE.mix, bed_below_speech_lu=22.0),
            sfx=replace(_BASE.sfx, max_per_minute=3.0),
            original_audio=replace(_BASE.original_audio, music_over_preserved="duck"),
        ),
        _format(
            "compilation",
            "Product-video compilation: manufacturer audio and bangs dominate; music only around"
            " them.",
            music=replace(
                _BASE.music,
                presence=replace(_BASE.music.presence, body=_OFF, explanation=_OFF),
                categories=replace(
                    _BASE.music.categories, outro=("festive_new_year", "hype_energetic")
                ),
            ),
            sfx=replace(_BASE.sfx, max_per_minute=3.0),
            original_audio=replace(_BASE.original_audio, source_audio_is_content=True),
        ),
        _format(
            "nye_uncut",
            "Midnight uncut: one continuous take; the fireworks are the soundtrack.",
            music=replace(
                _BASE.music,
                presence=RolePresence(
                    hook=_OFF,
                    intro=_OFF,
                    body=_OFF,
                    explanation=_OFF,
                    demonstration=_OFF,
                    reveal=_OFF,
                    recap=_OFF,
                    call_to_action=_OFF,
                    outro=_OFF,
                ),
            ),
            sfx=replace(_BASE.sfx, enabled=False),
            original_audio=replace(_BASE.original_audio, source_audio_is_content=True),
        ),
        _format(
            "shorts_meme",
            "Meme Short: one loopable shot with music in front; a pop on an emoji is allowed.",
            music=replace(
                _BASE.music,
                no_section_role=SectionRole.HOOK.value,
                presence=replace(_BASE.music.presence, body=_FEATURE),
                categories=replace(
                    _BASE.music.categories,
                    hook=("funny_meme", "hype_energetic"),
                    body=("funny_meme", "upbeat_positive"),
                ),
                min_cue_seconds=2.0,
                fade_out_seconds=0.5,
            ),
            sfx=replace(_BASE.sfx, max_per_minute=12.0, min_spacing_seconds=1.0),
        ),
        _format(
            "shorts_product_test",
            "Product-test Short: title card with music, then the raw night test with its own"
            " sound.",
            music=replace(_BASE.music, min_cue_seconds=1.5, fade_out_seconds=0.5),
            original_audio=replace(_BASE.original_audio, source_audio_is_content=True),
        ),
        _format(
            "shorts_versus",
            "Versus poll Short (r8A57tnbyKA, 8 s, no speech, -21.4 LUFS): A -> VS -> B -> clock ->"
            " result; music in front with a little tension for the clock, effects on the poll"
            " graphics.",
            music=replace(
                _BASE.music,
                no_section_role=SectionRole.HOOK.value,
                presence=replace(_BASE.music.presence, body=_FEATURE),
                categories=replace(
                    _BASE.music.categories,
                    hook=("suspense_build", "hype_energetic"),
                    body=("suspense_build", "upbeat_positive"),
                ),
                min_cue_seconds=2.0,
                fade_out_seconds=0.5,
            ),
            sfx=replace(_BASE.sfx, max_per_minute=12.0, min_spacing_seconds=1.0),
        ),
        _format(
            "shorts_haul",
            "Haul Short: fast voice-over montage (about 220 wpm) over an energetic bed; a register"
            " sound on prices.",
            music=replace(
                _BASE.music,
                categories=replace(
                    _BASE.music.categories,
                    body=("hype_energetic", "upbeat_positive"),
                    hook=("hype_energetic",),
                ),
                min_cue_seconds=2.0,
                fade_out_seconds=0.5,
            ),
            sfx=replace(_BASE.sfx, max_per_minute=12.0, min_spacing_seconds=1.0),
        ),
    )
}
DEFAULT_PROFILE: Final = "generic"

#: Story format (story_architecture) -> format profile.
STORY_FORMAT_PROFILE: Final[dict[str, str]] = {
    "generic": "generic",
    "top_n": "ranking",
    "product_introduction": "brochure_review",
    "review": "brochure_review",
    "unboxing": "unboxing",
    "talking_head": "generic",
    "tutorial": "generic",
    "interview": "generic",
    "podcast": "generic",
    "documentary": "compilation",
    "short": "shorts_haul",
}


# --- configuration -------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SectionOverride:
    """Overrides for one timeline section (dotted paths, e.g. ``music.presence.body``)."""

    section_id: str
    overrides: Mapping[str, OverrideValue]


@dataclass(frozen=True, slots=True)
class SoundDesignConfig:
    #: A built-in profile name; ``None`` -> by story format -> ``DEFAULT_PROFILE``.
    profile: str | None = None
    #: A profile built in code wins over ``profile``.
    custom: SoundProfile | None = None
    #: Project overrides by dotted path, e.g. ``{"mix.bed_below_speech_lu": 20}``.
    overrides: Mapping[str, OverrideValue] = field(default_factory=dict)
    section_overrides: tuple[SectionOverride, ...] = ()
    licence: LicencePolicy = field(default_factory=LicencePolicy)

    def resolve(self, story_format: str | None = None) -> SoundProfile:
        if self.custom is not None:
            base = self.custom
        else:
            name = self.profile or STORY_FORMAT_PROFILE.get(story_format or "", DEFAULT_PROFILE)
            if name not in PROFILES:
                raise InvalidSoundConfig(
                    f"unknown profile {name!r}; choose one of {', '.join(sorted(PROFILES))}"
                )
            base = PROFILES[name]
        profile = apply_overrides(base, self.overrides)
        problems = profile.problems()
        if problems:
            raise InvalidSoundConfig("; ".join(problems[:5]))
        return profile

    def for_section(self, profile: SoundProfile, section_id: str) -> SoundProfile:
        found = [o for o in self.section_overrides if o.section_id == section_id]
        for override in found:
            profile = apply_overrides(profile, override.overrides)
        if found and (problems := profile.problems()):
            raise InvalidSoundConfig(f"section {section_id}: " + "; ".join(problems[:5]))
        return profile

    def fingerprint(self, profile: SoundProfile) -> dict[str, object]:
        """Everything about the configuration that changes a plan (metadata never does)."""
        return {
            "profile": _plain(profile),
            "profile_schema": PROFILE_VERSION,
            "section_overrides": sorted(
                [o.section_id, sorted((k, _plain(v)) for k, v in o.overrides.items())]
                for o in self.section_overrides
            ),
            "licence": _plain(self.licence),
        }


def _plain(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value


def apply_overrides(profile: SoundProfile, overrides: Mapping[str, OverrideValue]) -> SoundProfile:
    for path, value in sorted(overrides.items()):
        profile = _override(profile, path.split("."), value, path)
    return profile


def _override(target: Any, parts: list[str], value: OverrideValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidSoundConfig(f"unknown setting {path!r}")
    if head in ("name", "identity", "identity_version", "rules"):
        raise InvalidSoundConfig(f"{path!r} cannot be overridden")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    return replace(instance, **{head: new})


def _coerce(current: object, value: OverrideValue, path: str) -> object:
    """``value`` as the type of the setting it replaces (int is accepted for float settings)."""
    if is_dataclass(current):
        raise InvalidSoundConfig(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, tuple):
        if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
            return tuple(value)
        raise InvalidSoundConfig(f"{path}: expected a list of names, got {value!r}")
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidSoundConfig(f"{path}: expected {type(current).__name__}, got {value!r}")
    return float(value) if isinstance(current, float) else value  # type: ignore[arg-type]
