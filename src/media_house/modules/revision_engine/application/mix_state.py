"""Which audio mix is in effect: the specification the latest mix result was made from.

The newest stored specification is not necessarily the one in use: asking for a mix whose
specification already exists reuses the stored one. The result names the hash of what it rendered,
so that is what is followed.
"""

from media_house.modules.audio_mix_engine.application.contracts import (
    AudioMixSpecification,
    MixCatalog,
)
from media_house.shared.errors import Err


def current_spec(
    mixes: MixCatalog, anchor_id: str, timeline_id: str | None
) -> AudioMixSpecification | None:
    result = mixes.latest_result(anchor_id, timeline_id)
    if not isinstance(result, Err) and result.value is not None:
        found = mixes.find_spec(anchor_id, result.value[0].specification_hash)
        if not isinstance(found, Err) and found.value is not None:
            return found.value[0]
    latest = mixes.latest_spec(anchor_id, timeline_id)
    if isinstance(latest, Err) or latest.value is None:
        return None
    return latest.value[0]


def current_overrides(
    mixes: MixCatalog, anchor_id: str, timeline_id: str | None
) -> tuple[str, ...]:
    spec = current_spec(mixes, anchor_id, timeline_id)
    return spec.provenance.overrides if spec is not None else ()
