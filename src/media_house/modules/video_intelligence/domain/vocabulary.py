"""The zero-shot vocabulary: what the embedding model is asked about, as data.

Adding a content type, an environment or a junk indicator is an edit of these tables plus a bump
of ``VOCABULARY_VERSION`` (the text embeddings are part of the embedding signals, so the version
is part of their cache key). No analyzer or derivation changes.

Labels only DESCRIBE what a picture looks like. ``CONTENT_PROFILE_OF`` maps a content label to the
score-conditioning profile it selects (see ``profiles.CONTENT_PROFILES``); unknown labels fall
back to ``generic``.
"""

VOCABULARY_VERSION = 1

#: label -> the sentence the model compares pictures with
CONTENT_TYPES: dict[str, str] = {
    "talking_head": "a photo of one person talking to the camera",
    "interview": "a photo of an interview between two people sitting",
    "screen_recording": "a screenshot of a computer screen with windows and text",
    "vlog": "a handheld selfie video of a person outdoors",
    "tutorial": "a tutorial showing a software interface or a how-to demonstration",
    "product_demo": "a product shown close up on a table",
    "gameplay": "a screenshot of a video game",
    "event": "a crowd of people at an event",
    "other": "a photo",
}

#: label -> sentence; describes where the picture was taken
ENVIRONMENTS: dict[str, str] = {
    "indoor": "a photo taken indoors inside a room",
    "outdoor": "a photo taken outdoors in daylight",
    "office": "a photo of an office with desks and computers",
    "studio": "a photo of a studio with lights and a backdrop",
    "home": "a photo of a living room or a kitchen at home",
    "street": "a photo of a city street",
    "nature": "a photo of nature, trees, mountains or a beach",
}

#: label -> sentence; signs that footage is not usable material. Flags only, never acted on here.
INDICATORS: dict[str, str] = {
    "clapperboard": "a film clapperboard slate",
    "covered_lens": "a blurry dark picture of a finger or hand covering the camera lens",
    "pocket": "a dark blurry picture taken inside a pocket or a bag",
}

CONTENT_PROFILE_OF: dict[str, str] = {
    "talking_head": "talking_head",
    "interview": "talking_head",
    "screen_recording": "tutorial_screen",
    "tutorial": "tutorial_screen",
    "vlog": "vlog",
}

CONTENT_PREFIX = "content:"
ENVIRONMENT_PREFIX = "environment:"
INDICATOR_PREFIX = "indicator:"


def label_prompts() -> tuple[tuple[str, str], ...]:
    """Every (prefixed label, sentence) in a fixed order: the vocabulary the embeddings carry."""
    return (
        *((CONTENT_PREFIX + k, v) for k, v in CONTENT_TYPES.items()),
        *((ENVIRONMENT_PREFIX + k, v) for k, v in ENVIRONMENTS.items()),
        *((INDICATOR_PREFIX + k, v) for k, v in INDICATORS.items()),
    )
