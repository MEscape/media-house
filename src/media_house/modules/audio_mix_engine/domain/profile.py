"""Mix profiles and delivery profiles: reusable technical behaviour, as data.

A mix profile says HOW the buses are balanced and processed (levels, EQ that makes room for the
voice, bus dynamics, how music ducks under speech). It makes no creative sound-design decision:
what music, effects and ambience belong in the video was decided upstream. A delivery profile says
what the finished file must satisfy (loudness, true peak, format). Both are chosen explicitly,
else from the format profile the creator identity already names, else the default; nothing is a
single constant for every video.
"""

from dataclasses import dataclass, field

from media_house.modules.audio_mix_engine.domain.errors import InvalidMixConfig
from media_house.modules.audio_mix_engine.domain.levels import LevelTargets
from media_house.modules.audio_mix_engine.domain.values import PROFILE_VERSION, BusRole


@dataclass(frozen=True, slots=True)
class EqBand:
    #: highpass | lowpass | peaking | lowshelf | highshelf | notch
    kind: str
    frequency_hz: float
    gain_db: float = 0.0
    q: float = 1.0


@dataclass(frozen=True, slots=True)
class Compressor:
    threshold_db: float
    ratio: float
    attack_ms: float
    release_ms: float
    makeup_db: float = 0.0


@dataclass(frozen=True, slots=True)
class Limiter:
    ceiling_db: float
    attack_ms: float = 5.0
    release_ms: float = 80.0


@dataclass(frozen=True, slots=True)
class BusProfile:
    role: BusRole
    gain_db: float = 0.0
    eq: tuple[EqBand, ...] = ()
    compressor: Compressor | None = None
    pan: float = 0.0


@dataclass(frozen=True, slots=True)
class DuckingProfile:
    """Used only when the sound design gave no ducking of its own (``auto_duck``)."""

    depth_db: float = 9.0
    attack_seconds: float = 0.25
    release_seconds: float = 0.6
    #: Speech gaps shorter than this keep the music down (no pumping between words).
    hold_seconds: float = 0.8


@dataclass(frozen=True, slots=True)
class MixProfile:
    key: str
    description: str
    buses: tuple[BusProfile, ...]
    ducking: DuckingProfile = field(default_factory=DuckingProfile)
    auto_duck: bool = True
    #: Dialogue clips are brought toward this integrated loudness (a fraction of the way, so
    #: natural differences between recordings survive).
    dialogue_target_lufs: float = -18.0
    dialogue_levelling: float = 0.7
    max_dialogue_trim_db: float = 6.0
    master_eq: tuple[EqBand, ...] = ()
    master_compressor: Compressor | None = None
    master_limiter: Limiter = field(default_factory=lambda: Limiter(-1.5))
    #: Validation: the dialogue should sit at least this far above the music (LU).
    min_dialogue_over_music_lu: float = 8.0
    #: Where music and effects sit against the levelled dialogue (``levels.py``).
    levels: LevelTargets = field(default_factory=LevelTargets)
    version: int = PROFILE_VERSION

    def bus(self, role: BusRole) -> BusProfile:
        return next((b for b in self.buses if b.role is role), BusProfile(role))


@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    key: str
    integrated_lufs: float
    #: The measured integrated loudness may differ from the target by this much (LU).
    tolerance_lu: float
    max_loudness_range_lu: float | None
    true_peak_dbtp: float
    sample_rate: int
    bit_depth: int
    channels: int
    container: str = "wav"
    #: A delivery heard on a phone speaker needs its own balance; ``None``: the mix profile's.
    levels: LevelTargets | None = None
    version: int = PROFILE_VERSION

    @property
    def codec(self) -> str:
        return {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}[self.bit_depth]


_VOICE_HP = EqBand("highpass", 80.0)
_VOICE_CLEAR = EqBand("peaking", 3000.0, 1.5, 1.0)
_MUSIC_MAKE_ROOM = EqBand("peaking", 2500.0, -2.5, 0.9)
_MUSIC_LOW = EqBand("highpass", 40.0)
_SFX_HP = EqBand("highpass", 60.0)

# The make-up gain gives back what the compressor takes at the dialogue target (measured on
# levelled speech at -18 LUFS: about 3 dB and 8 dB), so the dialogue bus stays at the level the
# music and effects are placed against.
_GENTLE_VOICE = Compressor(-20.0, 2.0, 15.0, 150.0, 3.0)
#: One voice carries the video: louder and softer words are held closer together.
_CREATOR_VOICE = Compressor(-26.0, 3.0, 8.0, 120.0, 8.0)


def _profile(
    key: str,
    description: str,
    *,
    music_db: float,
    sfx_db: float,
    ambience_db: float,
    duck_db: float,
    voice_comp: Compressor | None = _GENTLE_VOICE,
    min_gap: float = 8.0,
    levelling: float = 0.7,
    max_trim: float = 6.0,
    levels: LevelTargets | None = None,
) -> MixProfile:
    return MixProfile(
        key=key,
        description=description,
        buses=(
            BusProfile(BusRole.DIALOGUE, 0.0, (_VOICE_HP, _VOICE_CLEAR), voice_comp),
            BusProfile(BusRole.PRODUCTION, -2.0, (_VOICE_HP,), None),
            BusProfile(BusRole.MUSIC, music_db, (_MUSIC_LOW, _MUSIC_MAKE_ROOM), None),
            BusProfile(BusRole.SFX, sfx_db, (_SFX_HP,), None),
            BusProfile(BusRole.AMBIENCE, ambience_db, (_MUSIC_LOW,), None),
        ),
        ducking=DuckingProfile(depth_db=duck_db),
        dialogue_levelling=levelling,
        max_dialogue_trim_db=max_trim,
        min_dialogue_over_music_lu=min_gap,
        levels=levels or LevelTargets(),
    )


def _levels(under: float, gaps: float, impact: float, whoosh: float, ui: float) -> LevelTargets:
    return LevelTargets(
        music_under_speech_lu=under,
        music_in_gaps_lu=gaps,
        sfx_impact_lu=impact,
        sfx_whoosh_lu=whoosh,
        sfx_ui_lu=ui,
        sfx_other_lu=whoosh,
    )


#: A phone speaker in a busy feed: music closer under the voice, effects up front.
SHORTS_LEVELS = _levels(15.0, 7.0, 4.0, 8.0, 10.0)


PROFILES: tuple[MixProfile, ...] = (
    _profile(
        "generic",
        "balanced default",
        music_db=-6.0,
        sfx_db=-4.0,
        ambience_db=-10.0,
        duck_db=9.0,
        levels=_levels(16.0, 7.0, 5.0, 9.0, 11.0),
    ),
    _profile(
        "talking_head",
        "one voice carries the video: music stays well back",
        music_db=-9.0,
        sfx_db=-6.0,
        ambience_db=-12.0,
        duck_db=10.0,
        voice_comp=_CREATOR_VOICE,
        min_gap=12.0,
        # every take at the same level: a cut between two recordings must not jump in volume
        levelling=1.0,
        max_trim=10.0,
        levels=_levels(18.0, 8.0, 6.0, 10.0, 12.0),
    ),
    _profile(
        "tutorial",
        "speech must stay intelligible over long stretches",
        music_db=-11.0,
        sfx_db=-6.0,
        ambience_db=-14.0,
        duck_db=10.0,
        min_gap=11.0,
        levels=_levels(18.0, 9.0, 6.0, 10.0, 12.0),
    ),
    _profile(
        "product_demo",
        "the product's own sound matters: production audio stays natural",
        music_db=-8.0,
        sfx_db=-5.0,
        ambience_db=-9.0,
        duck_db=8.0,
        levels=_levels(16.0, 7.0, 5.0, 9.0, 11.0),
    ),
    _profile(
        "cinematic",
        "music and sound carry the picture: wider dynamics, a lighter touch on the voice",
        music_db=-4.0,
        sfx_db=-3.0,
        ambience_db=-8.0,
        duck_db=7.0,
        voice_comp=None,
        min_gap=6.0,
        levels=_levels(12.0, 5.0, 3.0, 7.0, 10.0),
    ),
)

DELIVERIES: tuple[DeliveryProfile, ...] = (
    DeliveryProfile("youtube", -14.0, 1.0, None, -1.0, 48000, 24, 2),
    DeliveryProfile("shorts", -14.0, 1.0, 12.0, -1.0, 48000, 24, 2, levels=SHORTS_LEVELS),
    DeliveryProfile("social", -14.0, 1.0, 12.0, -1.0, 48000, 16, 2, levels=SHORTS_LEVELS),
    DeliveryProfile("broadcast_r128", -23.0, 0.5, 15.0, -1.0, 48000, 24, 2),
)

DEFAULT_PROFILE = "generic"
DEFAULT_DELIVERY = "youtube"

#: The creator identity already names a format profile; these are the mix profile and delivery it
#: implies (nothing new about the creator is stored here).
_FORMAT_TO_PROFILE = {
    "talking_head": "talking_head",
    "tutorial": "tutorial",
    "review": "talking_head",
    "product_review": "product_demo",
    "product_demo": "product_demo",
    "cinematic": "cinematic",
}


def level_targets(profile: MixProfile, delivery: DeliveryProfile) -> LevelTargets:
    """The delivery's own balance where it has one (Shorts), else the mix profile's."""
    return delivery.levels or profile.levels


def mix_profile(key: str) -> MixProfile:
    found = next((p for p in PROFILES if p.key == key), None)
    if found is None:
        raise InvalidMixConfig(
            f"unknown mix profile {key!r} ({', '.join(p.key for p in PROFILES)})"
        )
    return found


def delivery_profile(key: str) -> DeliveryProfile:
    found = next((d for d in DELIVERIES if d.key == key), None)
    if found is None:
        raise InvalidMixConfig(
            f"unknown delivery profile {key!r} ({', '.join(d.key for d in DELIVERIES)})"
        )
    return found


def choose_profile(explicit: str | None, format_profile: str) -> MixProfile:
    """Explicit choice, else what the creator identity's format profile implies, else default."""
    if explicit:
        return mix_profile(explicit)
    return mix_profile(_FORMAT_TO_PROFILE.get(format_profile, DEFAULT_PROFILE))


def choose_delivery(explicit: str | None, format_profile: str) -> DeliveryProfile:
    if explicit:
        return delivery_profile(explicit)
    return delivery_profile("shorts" if format_profile.startswith("shorts") else DEFAULT_DELIVERY)
