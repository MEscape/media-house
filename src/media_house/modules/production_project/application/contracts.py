"""Public surface of production_project: read a scripted transcript and relate recordings to it.

Pure functions over plain data: callers (the pipeline CLI, a project UI) hand in the stored
transcript lines and get the recording order and the segments the script excludes.
"""

from media_house.modules.production_project.domain.conform import (
    Conformance,
    SegmentMatch,
    Take,
    TranscriptLine,
    conform,
    sentence_lines,
)
from media_house.modules.production_project.domain.script import (
    Exclusion,
    Scene,
    Script,
    ScriptSentence,
    parse_script,
)

__all__ = [
    "Conformance",
    "Exclusion",
    "Scene",
    "Script",
    "ScriptSentence",
    "SegmentMatch",
    "Take",
    "TranscriptLine",
    "conform",
    "parse_script",
    "sentence_lines",
]
