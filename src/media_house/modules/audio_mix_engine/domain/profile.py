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
    version: int = PROFILE_VERSION

    @property
    def codec(self) -> str:
        return {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}[self.bit_depth]


_VOICE_HP = EqBand("highpass", 80.0)
_VOICE_CLEAR = EqBand("peaking", 3000.0, 1.5, 1.0)
_MUSIC_MAKE_ROOM = EqBand("peaking", 2500.0, -2.5, 0.9)
_MUSIC_LOW = EqBand("highpass", 40.0)
_SFX_HP = EqBand("highpass", 60.0)

_GENTLE_VOICE = Compressor(-20.0, 2.0, 15.0, 150.0, 1.0)


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
        min_dialogue_over_music_lu=min_gap,
    )


PROFILES: tuple[MixProfile, ...] = (
    _profile(
        "generic", "balanced default", music_db=-6.0, sfx_db=-4.0, ambience_db=-10.0, duck_db=9.0
    ),
    _profile(
        "talking_head",
        "one voice carries the video: music stays well back",
        music_db=-9.0,
        sfx_db=-6.0,
        ambience_db=-12.0,
        duck_db=10.0,
        min_gap=10.0,
    ),
    _profile(
        "tutorial",
        "speech must stay intelligible over long stretches",
        music_db=-11.0,
        sfx_db=-6.0,
        ambience_db=-14.0,
        duck_db=10.0,
        min_gap=11.0,
    ),
    _profile(
        "product_demo",
        "the product's own sound matters: production audio stays natural",
        music_db=-8.0,
        sfx_db=-5.0,
        ambience_db=-9.0,
        duck_db=8.0,
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
    ),
)

DELIVERIES: tuple[DeliveryProfile, ...] = (
    DeliveryProfile("youtube", -14.0, 1.0, None, -1.0, 48000, 24, 2),
    DeliveryProfile("shorts", -14.0, 1.0, 12.0, -1.0, 48000, 24, 2),
    DeliveryProfile("social", -14.0, 1.0, 12.0, -1.0, 48000, 16, 2),
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
