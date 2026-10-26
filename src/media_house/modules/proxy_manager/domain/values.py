"""Vocabulary of proxy management: operation identity, versions and the enums every layer shares."""

from enum import StrEnum

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

#: Library derivation operation and ``processing_type`` metadata value of every proxy artifact.
PROXY_OPERATION = "proxy"
#: Key of the self-describing provenance record in the artifact's library metadata.
METADATA_KEY = "proxy"
#: Version of the ResolvedSpec layout and meaning; part of the library derivation fingerprint.
SPEC_VERSION = 1
#: Revision of how FFmpeg commands are built; bump when the same spec would yield different output.
ADAPTER_REVISION = "1"
#: Revision of the validation policy. A stored verdict from an older one is re-checked, not trusted.
VALIDATOR_VERSION = 1


class Codec(StrEnum):
    H264 = "h264"
    PRORES = "prores"


class AudioPolicy(StrEnum):
    """Which source audio streams a proxy carries."""

    FIRST = "first"
    ALL = "all"


class Verdict(StrEnum):
    VALID = "valid"
    VALID_WITH_TRANSFORMS = "valid_with_transforms"
    INVALID = "invalid"
    UNVERIFIABLE = "unverifiable"

    @property
    def usable(self) -> bool:
        return self in (Verdict.VALID, Verdict.VALID_WITH_TRANSFORMS)


class Evidence(StrEnum):
    """What a verdict rests on, weakest first. A verdict never claims more than its evidence."""

    # The probe adapter lists every packet; a headers-only level would never be produced.

    PACKETS = "packets"  # every packet listed: complete and consistent, NOT proven decodable
    DECODED = "decoded"  # decoded to the end without a single decoder complaint


class Condition(StrEnum):
    """Where a slot stands now. Computed against the live library, never stored."""

    NONE = "none"  # no proxy was ever activated
    READY = "ready"  # usable and exactly what the current request would produce
    STALE = "stale"  # usable, but the current request would resolve to a different spec
    NEEDS_REVALIDATION = "needs_revalidation"  # verdict made by an older validator
    MISSING = "missing"  # the library asset or its file is unavailable: deleted, lost or archived
    INVALID = "invalid"  # failed or could not complete validation
