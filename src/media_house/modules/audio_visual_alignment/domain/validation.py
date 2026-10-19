"""Checks a finished document against its own contract. Reports; never repairs."""

import math
from collections import Counter

from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument


def validate_document(document: AlignmentDocument) -> list[str]:
    """Human-readable problems; an empty list means the document is consistent."""
    problems: list[str] = []
    ids: list[str] = [
        *(s.source_id for s in document.speech_sources),
        *(link.link_id for link in document.speaker_links),
        *(m.match_id for m in document.text_matches),
        *(p.pair_id for p in document.sound_actions),
        *(b.relation_id for b in document.boundaries),
        *(o.opportunity_id for o in document.opportunities),
    ]
    problems += [f"duplicate id {i}" for i, n in Counter(ids).items() if n > 1]

    spans = [
        *((s.source_id, s.start, s.end) for s in document.speech_sources),
        *((m.match_id, m.start, m.end) for m in document.text_matches),
        *((o.opportunity_id, o.start, o.end) for o in document.opportunities),
        *((p.pair_id, p.audio_start, p.audio_end) for p in document.sound_actions),
    ]
    for name, start, end in spans:
        if not (math.isfinite(start) and math.isfinite(end)) or end < start:
            problems.append(f"{name}: invalid interval {start}..{end}")
    for correction in document.sync.corrections:
        if not math.isfinite(correction.seconds):
            problems.append(f"clock correction for {correction.stream} is not finite")
    shots = {p.shot_id for s in document.speech_sources for p in s.shots}
    for link in document.speaker_links:
        missing = set(link.shot_ids) - shots
        if missing:
            problems.append(f"{link.link_id}: refers to unknown shots {sorted(missing)}")
    problems += [f"{m.match_id}: not ranked" for m in document.text_matches if m.rank < 1]
    return problems
