"""Upstream results -> the plain facts the domain reads. The only place that knows their shapes.

* Picture: ``video_intelligence.edge_state`` at a moment just inside each clip (a moment the
  analysis did not track gives no subject, not a guess); names are read from the text on screen.
* Sound: the recording's own audio analysis around the boundary (the word it falls inside, a
  breath, the level and the quiet floor next to it, the nearest pause).
* Sync: the recording's alignment probe verdict.

Times: the audio analysis and the edit use the common (container) clock; the video analysis' clock
is shifted by the alignment's video correction, so a container second is looked up in the picture at
``second - correction``.
"""

import hashlib
from fractions import Fraction
from statistics import median

from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.audio_visual_alignment.application.contracts import (
    AlignmentDocument,
    ProbeState,
)
from media_house.modules.content_intelligence.application.contracts import designations
from media_house.modules.continuity_intelligence.domain.facts import (
    AudioEdge,
    EdgeFact,
    NameFact,
    SubjectFact,
)
from media_house.modules.video_intelligence.application.contracts import (
    EdgeState,
    VideoAnalysis,
    edge_state,
)

#: Seconds inside the clip at which the picture is read (the very last frame may be a transition).
INSIDE = 0.04
_WORD_MARGIN = 0.02
_NEAR_SPEECH = 0.15
_LEVEL_WINDOW = 0.3
_FLOOR_WINDOW = 1.0
_PAUSE_REACH = 0.6


def digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def frac(seconds: float) -> Fraction:
    return Fraction(seconds).limit_denominator(1000)


# --- picture ---------------------------------------------------------------------------------
def edge_fact(
    analysis: VideoAnalysis, moment: Fraction, outgoing: bool, correction: float
) -> tuple[EdgeFact, str | None, str | None] | None:
    """The frame at the boundary, with its shot and scene ids (``None``: outside every shot)."""
    inside = float(moment) + (-INSIDE if outgoing else INSIDE)
    state = edge_state(analysis, inside - correction)
    if state is None:
        return None
    return _fact(state), state.shot_id, state.scene_id


def _fact(s: EdgeState) -> EdgeFact:
    subject = s.subject
    return EdgeFact(
        subject=None
        if subject is None
        else SubjectFact(
            track_id=subject.track_id,
            identity_cluster=subject.identity_cluster,
            cx=subject.cx,
            cy=subject.cy,
            height=subject.height,
            vx=subject.vx,
            vy=subject.vy,
        ),
        head_yaw=s.head_yaw,
        eye_contact=s.eye_contact,
        visual_speaking=s.visual_speaking,
        framing=s.framing,
        camera_movement=s.camera_movement,
        luma=s.luma,
        color_balance=s.color_balance,
        subject_to_surround=s.subject_to_surround,
        backlit=s.backlit,
        environment=s.environment,
        indoor_outdoor=s.indoor_outdoor,
        objects=s.objects,
        names=tuple(
            dict.fromkeys(
                NameFact(family=d.family, value=d.value, text=d.text)
                for text in s.texts
                for d in designations(text)
            )
        ),
        embedding=s.embedding,
        embedding_model=s.embedding_model,
    )


# --- sound -------------------------------------------------------------------------------------
def audio_edge(timeline: AudioIntelligenceTimeline, moment: Fraction, outgoing: bool) -> AudioEdge:
    t = float(moment)
    word = timeline.word_at(t)
    straddled = None
    distance = None
    if word is not None and word.start + _WORD_MARGIN < t < word.end - _WORD_MARGIN:
        straddled = word.normalized_word
        distance = round(min(t - word.start, word.end - t), 4)
    side = (t - _LEVEL_WINDOW, t) if outgoing else (t, t + _LEVEL_WINDOW)
    floor_side = (t - _FLOOR_WINDOW, t) if outgoing else (t, t + _FLOOR_WINDOW)
    levels = [f.rms_db for f in timeline.frames_between(*side)]
    floor = sorted(f.rms_db for f in timeline.frames_between(*floor_side))
    return AudioEdge(
        straddled_word=straddled,
        word_edge_distance=distance,
        inside_breath=bool(timeline.events_between(t - 0.02, t + 0.02, kinds=("breath",))),
        speech_near=bool(timeline.words_between(t - _NEAR_SPEECH, t + _NEAR_SPEECH)),
        level_db=round(median(levels), 2) if levels else None,
        floor_db=round(floor[max(0, len(floor) // 10)], 2) if floor else None,
        pause_before=_pause(timeline, t, before=True),
        pause_after=_pause(timeline, t, before=False),
    )


def _pause(timeline: AudioIntelligenceTimeline, t: float, *, before: bool) -> Fraction | None:
    """The middle of the nearest real silence within reach before / after the boundary."""
    best: tuple[float, float] | None = None
    for p in timeline.pauses:
        if p.silence_ratio is not None and p.silence_ratio < 0.8:
            continue
        mid = (p.start + p.end) / 2
        gap = t - mid if before else mid - t
        if -0.05 <= gap <= _PAUSE_REACH and (best is None or gap < best[0]):
            best = (gap, mid)
    return None if best is None else frac(best[1])


def sync_state(document: AlignmentDocument | None) -> str:
    if document is None:
        return "unknown"
    return {
        ProbeState.CONSISTENT: "consistent",
        ProbeState.OFFSET_INDICATED: "offset_indicated",
        ProbeState.AMBIGUOUS: "ambiguous",
        ProbeState.INSUFFICIENT: "insufficient",
    }[document.sync.probe.state]
