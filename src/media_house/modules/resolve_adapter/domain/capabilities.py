"""What the installed Resolve can do, operation by operation.

Resolve's scripting API does not cover everything a timeline needs (no scripted transitions, no
retime, no native effects, no keyframed crops, no audio fades or volume keyframes). This registry
states, for every operation the adapter uses, how it is carried out, what limits it has, what
could replace it and how it is verified. A method that exists is not a capability that works: a
capability is ``SUPPORTED`` only after a read-back confirmed the whole operation on THIS Resolve
(version and edition); until then it is ``NOT_VERIFIED``. A known gap stays ``UNSUPPORTED`` and is
reported wherever a timeline element needs it, never silently replaced.

The registry is written from the published scripting documentation. It could not be checked
against an installed Resolve while this was written; the matrix says so by never claiming more
than ``NOT_VERIFIED`` until a live run confirms an operation.
"""

from dataclasses import dataclass

from media_house.modules.resolve_adapter.domain.environment import ResolveEnvironment
from media_house.modules.resolve_adapter.domain.values import CapabilityState


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    operation: str
    summary: str
    #: ``native_api``, ``project_setting``, ``marker`` (a person acts), ``none`` (a known gap).
    mechanism: str = "native_api"
    #: ``"Class.Method"`` names that must exist.
    methods: tuple[str, ...] = ()
    min_version: tuple[int, ...] | None = None
    #: ``studio`` when the free edition lacks it.
    edition: str | None = None
    plugin: str | None = None
    parameters: str = ""
    limits: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    verification: str = ""
    #: The scripting API has no way to do it (as documented).
    known_gap: bool = False
    #: Even when confirmed, only a part is covered (``limits`` says which).
    partial: bool = False
    #: Who carries it out when this adapter cannot.
    owner: str = ""


REGISTRY: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        "connection.external_scripting",
        "A script outside Resolve can drive it",
        methods=("Resolve.GetProjectManager",),
        min_version=(17,),
        edition="studio",
        limits=(
            "needs Preferences > General > External scripting using: Local",
            "the free edition may only run scripts from inside Resolve",
        ),
        verification="GetVersion and GetProjectManager answer",
    ),
    CapabilitySpec(
        "project.create",
        "Create a project",
        methods=("ProjectManager.CreateProject", "ProjectManager.GetProjectListInCurrentFolder"),
        verification="the project is listed and opens",
    ),
    CapabilitySpec(
        "project.open",
        "Open a project",
        methods=("ProjectManager.LoadProject", "ProjectManager.GetCurrentProject"),
        verification="the current project has the requested name",
    ),
    CapabilitySpec(
        "project.save",
        "Save the project",
        methods=("ProjectManager.SaveProject",),
        verification="the call reports success",
    ),
    CapabilitySpec(
        "project.settings",
        "Set and read project settings",
        mechanism="project_setting",
        methods=("Project.SetSetting", "Project.GetSetting"),
        limits=("frame rate and resolution cannot change once a timeline holds media",),
        verification="every setting is read back and compared",
    ),
    CapabilitySpec(
        "project.backup",
        "Export the project as a backup",
        methods=("ProjectManager.ExportProject",),
        verification="the export call reports success and the file exists",
    ),
    CapabilitySpec(
        "media.import",
        "Import media into the media pool",
        methods=("MediaPool.ImportMedia",),
        verification="the pool item's file path equals the requested file",
    ),
    CapabilitySpec(
        "media.folders",
        "Organise the media pool in folders",
        methods=(
            "MediaPool.GetRootFolder",
            "MediaPool.AddSubFolder",
            "MediaPool.SetCurrentFolder",
        ),
        verification="the folder is listed under its parent",
    ),
    CapabilitySpec(
        "media.relink",
        "Relink offline media",
        methods=("MediaPool.RelinkClips",),
        verification="the item is online afterwards",
    ),
    CapabilitySpec(
        "media.proxy_link",
        "Attach a proxy to a pool item",
        methods=("MediaPoolItem.LinkProxyMedia",),
        limits=("whether the proxy is active is a playback setting the API may not show",),
        verification="the item reports a linked proxy",
    ),
    CapabilitySpec(
        "timeline.create",
        "Create an empty timeline",
        methods=("MediaPool.CreateEmptyTimeline", "Project.SetCurrentTimeline"),
        verification="the timeline is listed and current",
    ),
    CapabilitySpec(
        "timeline.tracks",
        "Add video and audio tracks",
        methods=("Timeline.AddTrack", "Timeline.GetTrackCount"),
        limits=("the audio track layout is chosen when the track is added",),
        verification="the track count equals the request",
    ),
    CapabilitySpec(
        "timeline.track_names",
        "Name tracks",
        methods=("Timeline.SetTrackName", "Timeline.GetTrackName"),
        verification="the name is read back",
    ),
    CapabilitySpec(
        "timeline.duplicate",
        "Duplicate a timeline (the safety copy before a rebuild)",
        methods=("Timeline.DuplicateTimeline",),
        verification="the copy is listed",
    ),
    CapabilitySpec(
        "clip.place",
        "Place a clip at a frame of a track with a source range",
        methods=("MediaPool.AppendToTimeline", "Timeline.GetItemListInTrack"),
        parameters="mediaPoolItem, startFrame, endFrame, mediaType, trackIndex, recordFrame",
        limits=(
            "whether endFrame is inclusive and what unit an audio-only clip uses are confirmed "
            "by reading the placed clip back (Quirks)",
        ),
        verification="start, duration and in point are read back from the placed item",
    ),
    CapabilitySpec(
        "clip.delete",
        "Remove clips",
        methods=("Timeline.DeleteClips",),
        verification="the item is gone from the track",
    ),
    CapabilitySpec(
        "clip.enable",
        "Enable or disable a clip",
        methods=("TimelineItem.SetClipEnabled", "TimelineItem.GetClipEnabled"),
        verification="the state is read back",
    ),
    CapabilitySpec(
        "clip.link",
        "Link a picture and its sound",
        methods=("Timeline.SetClipsLinked",),
        verification="the call reports success; the link itself cannot be read back",
    ),
    CapabilitySpec(
        "clip.transform",
        "Position, zoom and rotation of a clip",
        methods=("TimelineItem.SetProperty", "TimelineItem.GetProperty"),
        parameters="Pan, Tilt, ZoomX, ZoomY, RotationAngle",
        verification="every property is read back",
    ),
    CapabilitySpec(
        "clip.opacity",
        "Opacity of a clip",
        methods=("TimelineItem.SetProperty",),
        parameters="Opacity 0-100",
        verification="the property is read back",
    ),
    CapabilitySpec(
        "clip.crop_static",
        "A reframing window that does not change over the clip",
        methods=("TimelineItem.SetProperty",),
        parameters="Zoom and Pan/Tilt computed from the crop window",
        limits=("only a window that does not move",),
        partial=True,
        verification="the zoom and pan properties are read back",
    ),
    CapabilitySpec(
        "clip.crop_fusion",
        "A moving reframing window as a keyframed Fusion transform on the clip",
        methods=("TimelineItem.AddFusionComp",),
        parameters="Transform Size and Pivot keyframes with eased Bezier handles",
        limits=("the window must stay inside the picture (zoom 1 or more)",),
        partial=True,
        verification="the keyframes are read back from the composition",
    ),
    CapabilitySpec(
        "clip.grade_cdl",
        "A gentle auto grade as a CDL on the clip's first colour node",
        methods=("TimelineItem.SetCDL",),
        parameters="Slope, Offset, Power, Saturation",
        verification="the call reports success; the grade cannot be read back",
    ),
    CapabilitySpec(
        "clip.crop_keyframed",
        "A reframing window that moves over the clip",
        mechanism="none",
        known_gap=True,
        alternatives=(
            "a Fusion composition with keyframes",
            "a marker: a person keyframes the crop in Resolve",
        ),
        limits=("the scripting API has no keyframes for clip properties",),
        verification="not applicable",
    ),
    CapabilitySpec(
        "clip.retime",
        "Speed changes, ramps, freezes and reverse playback",
        mechanism="none",
        known_gap=True,
        alternatives=(
            "the VFX engine bakes the retime into the rendered clip (already the plan's choice)",
            "a timeline interchange file with the retime",
            "a marker: a person applies Change Clip Speed in Resolve",
        ),
        limits=("the scripting API has no clip speed",),
        verification="not applicable",
    ),
    CapabilitySpec(
        "transition.insert",
        "A transition between two clips",
        mechanism="none",
        known_gap=True,
        alternatives=(
            "a timeline interchange file (FCPXML / OTIO) that carries the transition",
            "a marker at the cut: a person adds it in Resolve (the handles are in place)",
        ),
        limits=("the scripting API has no way to add or change a transition",),
        verification="not applicable",
    ),
    CapabilitySpec(
        "effect.native",
        "A native Resolve effect on a clip",
        mechanism="none",
        known_gap=True,
        alternatives=(
            "an effect the VFX engine baked into the clip",
            "a marker: a person applies the effect in Resolve",
        ),
        limits=("the scripting API cannot add OpenFX or Resolve FX to a clip",),
        verification="not applicable",
    ),
    CapabilitySpec(
        "audio.gain_fades",
        "Audio fades, ducking and volume changes over a clip",
        mechanism="none",
        known_gap=True,
        owner="audio_mix_engine",
        alternatives=("the final mix is carried out by audio_mix_engine, not by this adapter",),
        limits=("the scripting API has no audio fades and no volume keyframes",),
        verification="not applicable",
    ),
    CapabilitySpec(
        "audio.clip_volume",
        "A static volume per audio clip (the mix's level of the clip)",
        methods=("TimelineItem.SetProperty", "TimelineItem.GetProperty"),
        parameters="AudioVolume -100.0 to 30.0 dB",
        limits=(
            "one level for the whole clip: no ducking, fades or bus processing",
            "a Resolve that refuses the property plays the clip at 0 dB (reported, not a failure)",
        ),
        alternatives=("the final mix of audio_mix_engine",),
        verification="the volume is read back",
    ),
    CapabilitySpec(
        "caption.subtitle_import",
        "Editable caption cues as a subtitle file in the media pool",
        methods=("MediaPool.ImportMedia",),
        partial=True,
        limits=(
            "the file is imported into the pool; placing it on a subtitle track is done by "
            "the person (drag) unless a read-back shows the track holds it",
        ),
        alternatives=("the rendered caption clips the plan made",),
        verification="the pool item exists; the subtitle track count is read back",
    ),
    CapabilitySpec(
        "marker.timeline",
        "Timeline markers",
        mechanism="marker",
        methods=("Timeline.AddMarker", "Timeline.GetMarkers"),
        verification="the marker is read back at its frame",
    ),
    CapabilitySpec(
        "marker.custom_data",
        "Markers that carry an identifier",
        mechanism="marker",
        methods=("Timeline.GetMarkerByCustomData", "Timeline.UpdateMarkerCustomData"),
        min_version=(17,),
        verification="the marker is found by its custom data",
    ),
    CapabilitySpec(
        "item.identity",
        "A stable id for a timeline item",
        methods=("TimelineItem.GetUniqueId",),
        min_version=(18,),
        alternatives=("the element tag marker on the item",),
        verification="the id is read",
    ),
    CapabilitySpec(
        "snapshot.read",
        "Read the timeline back",
        methods=(
            "Timeline.GetItemListInTrack",
            "TimelineItem.GetStart",
            "TimelineItem.GetDuration",
            "TimelineItem.GetLeftOffset",
        ),
        verification="the items of every track are listed",
    ),
    CapabilitySpec(
        "render.configure",
        "Render format, codec and settings",
        methods=(
            "Project.SetRenderSettings",
            "Project.SetCurrentRenderFormatAndCodec",
            "Project.GetRenderCodecs",
        ),
        verification="format and codec are read back",
    ),
    CapabilitySpec(
        "render.submit",
        "Queue and start a render",
        methods=("Project.AddRenderJob", "Project.StartRendering"),
        verification="a job id is returned and the job is listed",
    ),
    CapabilitySpec(
        "render.monitor",
        "Follow a render job",
        methods=("Project.GetRenderJobStatus", "Project.IsRenderingInProgress"),
        verification="the status reaches a final word",
    ),
    CapabilitySpec(
        "fonts.verify",
        "Check that the creator's fonts are installed",
        mechanism="none",
        known_gap=True,
        alternatives=("the builder's font references say which fonts the design needs",),
        limits=("the scripting API cannot list installed fonts",),
        verification="not applicable",
    ),
)

_BY_OPERATION = {s.operation: s for s in REGISTRY}


@dataclass(frozen=True, slots=True)
class DetectedCapability:
    operation: str
    state: CapabilityState
    reason: str
    mechanism: str
    summary: str
    limits: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    verification: str = ""
    owner: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityMatrix:
    items: tuple[DetectedCapability, ...]

    def get(self, operation: str) -> DetectedCapability:
        found = next((c for c in self.items if c.operation == operation), None)
        if found is None:
            raise KeyError(f"unknown capability {operation!r}")
        return found

    def state(self, operation: str) -> CapabilityState:
        return self.get(operation).state

    def usable(self, operation: str) -> bool:
        """May be attempted: it exists, and the attempt is verified by reading back."""
        return self.state(operation) in {
            CapabilityState.SUPPORTED,
            CapabilityState.PARTIALLY_SUPPORTED,
            CapabilityState.NOT_VERIFIED,
        }

    def unsupported(self) -> tuple[DetectedCapability, ...]:
        return tuple(
            c
            for c in self.items
            if c.state
            in {
                CapabilityState.UNSUPPORTED,
                CapabilityState.VERSION_DEPENDENT,
                CapabilityState.EDITION_DEPENDENT,
                CapabilityState.PLUGIN_DEPENDENT,
            }
        )


def spec_of(operation: str) -> CapabilitySpec:
    return _BY_OPERATION[operation]


def detect(
    env: ResolveEnvironment | None, observed: frozenset[str] = frozenset()
) -> CapabilityMatrix:
    """The capabilities of this Resolve. ``observed`` are the operations a read-back confirmed on
    this same Resolve (version and edition)."""
    return CapabilityMatrix(tuple(_detect_one(spec, env, observed) for spec in REGISTRY))


def _detect_one(
    spec: CapabilitySpec, env: ResolveEnvironment | None, observed: frozenset[str]
) -> DetectedCapability:
    state, reason = _state(spec, env, observed)
    return DetectedCapability(
        operation=spec.operation,
        state=state,
        reason=reason,
        mechanism=spec.mechanism,
        summary=spec.summary,
        limits=spec.limits,
        alternatives=spec.alternatives,
        verification=spec.verification,
        owner=spec.owner,
    )


def _state(
    spec: CapabilitySpec, env: ResolveEnvironment | None, observed: frozenset[str]
) -> tuple[CapabilityState, str]:
    if spec.known_gap:
        return CapabilityState.UNSUPPORTED, "the scripting API has no method for it"
    if env is None:
        return CapabilityState.NOT_VERIFIED, "no connection: nothing was checked"
    if spec.edition == "studio" and env.edition == "free" and spec.operation not in observed:
        return CapabilityState.EDITION_DEPENDENT, "needs the Studio edition"
    if (
        spec.min_version is not None
        and env.version is not None
        and not env.version.at_least(spec.min_version)
    ):
        need = ".".join(str(n) for n in spec.min_version)
        return CapabilityState.VERSION_DEPENDENT, f"needs Resolve {need} or newer"
    if spec.plugin is not None and env.plugins is not None and spec.plugin not in env.plugins:
        return CapabilityState.PLUGIN_DEPENDENT, f"needs {spec.plugin}"
    missing = [m for m in spec.methods if env.has_method(m) is False]
    if missing:
        return CapabilityState.UNSUPPORTED, "missing method: " + ", ".join(missing)
    if spec.operation in observed:
        if spec.partial:
            return CapabilityState.PARTIALLY_SUPPORTED, "confirmed on this Resolve, with limits"
        return CapabilityState.SUPPORTED, "confirmed on this Resolve"
    unprobed = [m for m in spec.methods if env.has_method(m) is None]
    if unprobed:
        return CapabilityState.NOT_VERIFIED, "its objects were not reachable yet"
    return CapabilityState.NOT_VERIFIED, "the methods exist; no read-back has confirmed it yet"
