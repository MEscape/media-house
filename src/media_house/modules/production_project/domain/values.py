"""Vocabulary of the production project: constraints, severities, roles, slots, artifacts."""

from enum import StrEnum


class ConstraintKind(StrEnum):
    """How a brief rule binds. ``HARD`` = a breach is a penalty or a rejection."""

    HARD = "hard"
    SOFT = "soft"
    #: Allowed only after somebody confirmed it.
    CONDITIONAL = "conditional"
    #: Must not be shown or shared before a date, or at all.
    CONFIDENTIAL = "confidential"


class Severity(StrEnum):
    #: Cannot be exported while it stands.
    BLOCKER = "blocker"
    WARNING = "warning"


class ElementKind(StrEnum):
    LABEL = "label"
    HASHTAG = "hashtag"
    HANDLE = "handle"
    TAG = "tag"
    LINK = "link"
    LINE = "line"


class Role(StrEnum):
    """What a file is for. The edit engine treats each role differently."""

    TALKING = "talking"
    GAGS = "gags"
    SCREENREC = "screenrec"
    PACKSHOT = "packshot"
    SCREENSHOT = "screenshot"
    ARCHIVE = "archive"
    BACKGROUND = "background"
    LOGO = "logo"
    BRAND = "brand"
    DOCUMENT = "document"


class Folder(StrEnum):
    """The numbered folders of a project (a template: a project may use more or fewer)."""

    CONTEXT = "00_kontext"
    CAM = "01_cam"
    SCR = "02_scr"
    PCK = "03_pck"
    ARC = "04_arc"
    BG = "05_bg"
    GFX = "06_gfx"


#: The folder each role lives in (the default template).
FOLDER_OF: dict[Role, Folder] = {
    Role.TALKING: Folder.CAM,
    Role.GAGS: Folder.CAM,
    Role.SCREENREC: Folder.SCR,
    Role.SCREENSHOT: Folder.SCR,
    Role.PACKSHOT: Folder.PCK,
    Role.DOCUMENT: Folder.PCK,
    Role.ARCHIVE: Folder.ARC,
    Role.BACKGROUND: Folder.BG,
    Role.LOGO: Folder.GFX,
    Role.BRAND: Folder.GFX,
}


class Priority(StrEnum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class MultiFile(StrEnum):
    SINGLE = "single"
    ORDERED_CHUNKS = "ordered-chunks"
    SET = "set"


class SlotStatus(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    FLAGGED = "flagged"


class Timing(StrEnum):
    """When a requirement can be recorded relative to the source going live."""

    ANYTIME = "anytime"
    BEFORE_SOURCE_LIVE = "before_source_live"
    AFTER_SOURCE_LIVE = "after_source_live"


class Artifact(StrEnum):
    """The derived pieces of a project; each says what it was generated from."""

    BRIEF = "brief"
    RISKS = "risks"
    CONCEPTS = "concepts"
    FACTS = "facts"
    SLOTS = "slots"
    SCRIPT = "script"
    GRAPHICS_INTENT = "graphics_intent"
    REQUIREMENTS = "requirements"
    CONTEXT = "context"
    METADATA = "metadata"
    KURZKONZEPT = "kurzkonzept"


#: What an artifact is derived from (so an edit marks the right dependants dirty).
DEPENDS_ON: dict[Artifact, tuple[Artifact, ...]] = {
    Artifact.BRIEF: (),
    Artifact.RISKS: (Artifact.BRIEF,),
    Artifact.CONCEPTS: (Artifact.BRIEF, Artifact.RISKS),
    Artifact.FACTS: (Artifact.BRIEF,),
    Artifact.SLOTS: (Artifact.FACTS,),
    # The script keeps ``{{fact:key}}`` and ``{SLOT}`` placeholders: a changed number or a filled
    # slot shows in it at once, so it is not stale, only what was written down from it is.
    Artifact.SCRIPT: (Artifact.CONCEPTS,),
    Artifact.GRAPHICS_INTENT: (Artifact.SCRIPT,),
    Artifact.REQUIREMENTS: (Artifact.SCRIPT, Artifact.BRIEF, Artifact.FACTS),
    Artifact.CONTEXT: (
        Artifact.BRIEF,
        Artifact.SCRIPT,
        Artifact.FACTS,
        Artifact.SLOTS,
        Artifact.REQUIREMENTS,
    ),
    Artifact.METADATA: (Artifact.BRIEF, Artifact.SCRIPT, Artifact.FACTS, Artifact.SLOTS),
    Artifact.KURZKONZEPT: (Artifact.BRIEF, Artifact.RISKS, Artifact.FACTS),
}


class FlagKind(StrEnum):
    THIRD_PARTY_BRAND = "third_party_brand"
    PRIVATE_DATA = "private_data"
    CONFIDENTIAL = "confidential"
    BYSTANDER_FACES = "bystander_faces"
    ILLEGAL_PYRO = "illegal_pyro"
    LICENSE_UNKNOWN = "license_unknown"
    RESOLUTION = "resolution"
    FPS = "fps"
    NO_AUDIO = "no_audio"
    CLIPPING = "clipping"
    LONG_SILENCE = "long_silence"


class Resolution(StrEnum):
    """How a person settled a flag. A flagged file is never used until it is settled."""

    OPEN = "open"
    APPROVED = "approved"
    BLURRED = "blurred"
    REPLACED = "replaced"
