"""Adapter configuration: what the person or the pipeline chooses, with field metadata for a UI.

Nothing here is a creative decision: the look, the cuts and the transitions come from the built
timeline. These are execution choices (what may be touched, where the folders go, what to do with
a clip Resolve cannot retime).
"""

from dataclasses import dataclass, field

from media_house.modules.resolve_adapter.domain.errors import InvalidAdapterConfig
from media_house.modules.resolve_adapter.domain.values import (
    CaptionStrategy,
    ExecutionPolicy,
    RetimePlacement,
)

#: The oldest Resolve the adapter drives (the documented scripting API this was written from).
MIN_RESOLVE_VERSION = (18, 0)


@dataclass(frozen=True, slots=True)
class Quirks:
    """Behaviour of the scripting API that a read-back must confirm on a real Resolve.

    The defaults are the documented behaviour. A wrong default does not corrupt a project: the
    placed clip is read back, so a mismatch is a failed verification, not a silent error."""

    #: ``endFrame`` of a placed clip is the last frame played (not the one after it).
    end_frame_inclusive: bool = True
    #: Audio-only clips are placed in frames of the timeline rate.
    audio_frames_follow_timeline: bool = True
    #: Timeline start timecode applied to the new timeline, so frame 0 is the first frame.
    timeline_start_timecode: str = "00:00:00:00"


@dataclass(frozen=True, slots=True)
class ResolveAdapterConfig:
    #: What the adapter may do to a project that already holds work.
    policy: ExecutionPolicy = field(
        default=ExecutionPolicy.REQUIRE_CONFLICT_REVIEW,
        metadata={"label": "Existing work", "group": "safety"},
    )
    #: Another project is open in Resolve: save it and switch? Off: the run stops instead.
    allow_project_switch: bool = field(
        default=False, metadata={"label": "Switch the open project", "group": "safety"}
    )
    #: A project with the target name exists and was not made by this adapter: use it?
    adopt_existing_project: bool = field(
        default=False, metadata={"label": "Use an existing project", "group": "safety"}
    )
    #: Name of the project; empty = derived from the recording's name.
    project_name: str = field(default="", metadata={"label": "Project name", "group": "project"})
    #: Prefix of the media pool folders the adapter creates.
    root_folder: str = field(default="Media House", metadata={"label": "Pool folder"})
    #: Put a tag marker on every clip so it can be recognised after a person moved it.
    tag_items: bool = field(default=True, metadata={"label": "Tag clips"})
    caption_strategy: CaptionStrategy = field(
        default=CaptionStrategy.RENDERED, metadata={"label": "Caption text", "group": "captions"}
    )
    retime_placement: RetimePlacement = field(
        default=RetimePlacement.PLACE_WITH_MARKER, metadata={"label": "Clip that needs a retime"}
    )
    #: Instruct a person with a marker where the API cannot act (transitions, retime, effects).
    instruction_markers: bool = field(default=True, metadata={"label": "Instruction markers"})
    #: Link proxies of the sources that have one (editing speed; the final render never uses them).
    link_proxies: bool = field(default=True, metadata={"label": "Link proxies"})
    #: The proxy profile editing uses (the proxy manager's name for it).
    proxy_profile: str = "edit"
    #: Save the project after the timeline is built.
    save: bool = True
    #: Block when a source file is missing or changed since the timeline was built.
    require_media: bool = True
    minimum_version: tuple[int, ...] = MIN_RESOLVE_VERSION
    quirks: Quirks = field(default_factory=Quirks)

    def __post_init__(self) -> None:
        if self.project_name != self.project_name.strip():
            raise InvalidAdapterConfig("the project name has leading or trailing spaces")
        if len(self.project_name) > 120:
            raise InvalidAdapterConfig("the project name is longer than 120 characters")
        if not self.root_folder.strip() or "/" in self.root_folder or "\\" in self.root_folder:
            raise InvalidAdapterConfig("the pool folder needs a plain name")
        if not self.minimum_version:
            raise InvalidAdapterConfig("a minimum version is required")


@dataclass(frozen=True, slots=True)
class RenderProfile:
    """Resolve's render choices for a delivery. The size and rate come from the timeline."""

    key: str
    #: Format ids to try in order (``GetRenderFormats`` ids), then codec ids of the format.
    formats: tuple[str, ...]
    codecs: tuple[str, ...]
    audio_codec: str
    audio_bitrate_kbps: int | None
    video_quality: str  # "auto" or "best" or a bitrate in kbps
    extension: str
    expected_video_codecs: tuple[str, ...]
    expected_audio_codecs: tuple[str, ...]


#: The delivery profiles. The frame size, rate and aspect always come from the timeline; only the
#: container and codec are chosen here.
RENDER_PROFILES: tuple[RenderProfile, ...] = (
    RenderProfile(
        key="youtube",
        formats=("mp4",),
        codecs=("H264", "H265"),
        audio_codec="aac",
        audio_bitrate_kbps=320,
        video_quality="best",
        extension="mp4",
        expected_video_codecs=("h264", "hevc"),
        expected_audio_codecs=("aac",),
    ),
    RenderProfile(
        key="master",
        formats=("mov",),
        codecs=("ProRes422HQ", "ProRes422"),
        audio_codec="lpcm",
        audio_bitrate_kbps=None,
        video_quality="best",
        extension="mov",
        expected_video_codecs=("prores",),
        expected_audio_codecs=("pcm_s16le", "pcm_s24le", "pcm_s32le"),
    ),
)


def render_profile(key: str) -> RenderProfile:
    found = next((p for p in RENDER_PROFILES if p.key == key), None)
    if found is None:
        raise InvalidAdapterConfig(
            f"unknown render profile {key!r} ({', '.join(p.key for p in RENDER_PROFILES)})"
        )
    return found


@dataclass(frozen=True, slots=True)
class RenderConfig:
    profile: str = "youtube"
    #: Name of the file without extension; empty = the timeline's name.
    file_name: str = ""
    #: Block the render when the final-quality checks fail (proxy media, open conflicts ...).
    require_final_quality: bool = True
    #: Seconds between status polls and the longest wait for one render.
    poll_seconds: float = 2.0
    timeout_seconds: float = 6 * 3600.0

    def __post_init__(self) -> None:
        render_profile(self.profile)
        if self.poll_seconds <= 0 or self.timeout_seconds <= 0:
            raise InvalidAdapterConfig("render poll and timeout must be positive")
