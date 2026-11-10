"""Word edges snapped to measured speech activity.

Forced alignment often stretches the last word before a pause across the whole silence
("4." from 17.37 s to 29.64 s while the voice stops at 17.8 s). Every pause downstream (cuts,
pacing, dead air) is then invisible. Here a word keeps only the speech it is made of:

* its END moves back to the last speech frame before the first silent run of at least
  ``min_silence`` (plus a short ``hangover`` for soft endings: "s", "t", breath-out);
* its START moves forward past a leading silent run of at least ``min_silence``, or back onto
  speech that directly precedes it (up to ``max_lead``) when the earlier word no longer owns it.

A word without any speech frame is left as it is (nothing measured says where it is), and a word
is only changed when at least ``min_change`` seconds go. Words never overlap or reorder; segment
bounds follow their words. Pure and deterministic.
"""

from dataclasses import replace

from media_house.modules.audio_intelligence.domain.analysis.acoustic import AcousticTrack
from media_house.modules.audio_intelligence.domain.transcript import (
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)


def snap_to_speech(
    transcript: Transcript,
    track: AcousticTrack,
    *,
    min_silence: float = 0.3,
    hangover: float = 0.06,
    max_lead: float = 0.3,
    min_change: float = 0.15,
) -> Transcript:
    if not transcript.words or not len(track):
        return transcript
    silent_run = max(1, round(min_silence / track.hop))
    lead_frames = max(0, round(max_lead / track.hop))
    snapped: dict[int, TranscriptWord] = {}
    floor = 0.0  # where the previous (snapped) word ends
    for word in transcript.words:
        frames = track.span(word.start, word.end)
        active = [f for f in frames if track.speech[f]]
        if not active:
            snapped[word.index] = word
            floor = max(floor, word.end)
            continue
        # A leading silence long enough to be a pause is not part of the word.
        first = active[0] if active[0] - frames.start >= silent_run else frames.start
        start = track.frame_start(first) if first > frames.start else word.start
        # Speech running straight into the word belongs to it once the earlier word lets go.
        lead = first
        while (
            first == frames.start
            and lead > 0
            and first - lead < lead_frames
            and track.speech[lead - 1]
            and track.frame_start(lead - 1) >= floor
        ):
            lead -= 1
        if lead < first:
            start = track.frame_start(lead)
        last = _speech_end(track, range(first, frames.stop), silent_run)
        end = min(word.end, track.frame_start(last) + track.hop + hangover)
        start = max(start, min(floor, word.start))  # never into the earlier word
        if end <= start or (word.end - end) + abs(word.start - start) < min_change:
            start, end = word.start, word.end
        moved = (start, end) != (word.start, word.end)
        snapped[word.index] = replace(word, start=start, end=end) if moved else word
        floor = max(floor, end)
    if all(snapped[w.index] is w for w in transcript.words):
        return transcript
    return Transcript(transcript.metadata, tuple(_segment(s, snapped) for s in transcript.segments))


def _speech_end(track: AcousticTrack, frames: range, silent_run: int) -> int:
    """The last speech frame before the first silence of ``silent_run`` frames (or the end)."""
    last = frames.start
    quiet = 0
    for f in frames:
        if track.speech[f]:
            last, quiet = f, 0
        else:
            quiet += 1
            if quiet >= silent_run:
                break
    return last


def _segment(segment: TranscriptSegment, words: dict[int, TranscriptWord]) -> TranscriptSegment:
    if not segment.words:
        return segment
    moved = tuple(words[w.index] for w in segment.words)
    if all(a is b for a, b in zip(moved, segment.words, strict=True)):
        return segment
    return replace(segment, start=moved[0].start, end=moved[-1].end, words=moved)
