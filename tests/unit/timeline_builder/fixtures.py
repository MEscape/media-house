"""Representative inputs for the builder: the REAL output of the compiler, mapped as the pipeline
maps it.

Compiled from the compiler's own fixture edit (a two-clip talking head at 25 fps with cover
footage, a linked voice, a graphic, captions, a dissolve, music and a sound effect). ``vfx=None``
by default so the cut between the two main clips is a real dissolve (a baked clip holds no handle).
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from media_house.modules.edit_plan_compiler.domain.engine import CompileMeta, compile_timeline
from media_house.modules.edit_plan_compiler.domain.model import TimelineSpecification
from media_house.modules.edit_plan_compiler.domain.profile import CompilerConfig
from media_house.modules.timeline_builder.application.views import spec_facts
from media_house.modules.timeline_builder.domain import facts as sf
from media_house.modules.timeline_builder.domain.engine import BuildMeta, Built, build_timeline
from media_house.modules.timeline_builder.domain.model import BuiltTimeline
from media_house.modules.timeline_builder.domain.profile import BuilderConfig
from tests.unit.edit_plan_compiler import fixtures as cf

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


def compiled(config: CompilerConfig | None = None, **kw: Any) -> TimelineSpecification:
    kw.setdefault("vfx", None)
    meta = CompileMeta(
        compilation_version=1,
        supersedes=None,
        compiled_at=cf.NOW,
        inputs=(),
        sound_identity="mesc_sound",
        sound_identity_version=1,
    )
    result = compile_timeline(cf.inputs(**kw), config or CompilerConfig(), meta=meta)
    assert not result.critical, [i.message for i in result.critical]
    return result.spec


def facts_of(config: CompilerConfig | None = None, **kw: Any) -> sf.SpecFacts:
    return spec_facts(compiled(config, **kw))


def live_for(
    facts: sf.SpecFacts, *, missing: tuple[str, ...] = (), gone: tuple[str, ...] = ()
) -> sf.LiveSources:
    """Every file present with the checksum the specification recorded, except ``missing``
    (not in the library) and ``gone`` (in the library, file not on disk)."""
    return sf.LiveSources(
        found={
            a.asset_id: sf.LiveSource(
                asset_id=a.asset_id,
                exists=a.asset_id not in missing,
                available=a.asset_id not in missing and a.asset_id not in gone,
                checksum=a.checksum,
                name=f"{a.asset_id}.mov",
                proxy_asset_id=f"proxy-{a.asset_id}" if a.asset_id == "rec1" else None,
            )
            for a in facts.assets
        }
    )


def meta(version: int = 1, supersedes: str | None = None, at: datetime = NOW) -> BuildMeta:
    return BuildMeta(
        build_version=version, supersedes=supersedes, built_at=at, spec_asset_id="spec-doc"
    )


def build(
    facts: sf.SpecFacts | None = None,
    *,
    live: sf.LiveSources | None = None,
    config: BuilderConfig | None = None,
    previous: BuiltTimeline | None = None,
    version: int = 1,
    at: datetime = NOW,
    **kw: Any,
) -> Built:
    facts = facts or facts_of()
    return build_timeline(
        facts,
        live or live_for(facts),
        config or BuilderConfig(),
        meta=meta(version, "prev" if previous else None, at),
        previous=previous,
        **kw,
    )


def built(facts: sf.SpecFacts | None = None, **kw: Any) -> BuiltTimeline:
    result = build(facts, **kw)
    assert result.timeline is not None, [e.message for e in result.errors]
    return result.timeline


def refused(facts: sf.SpecFacts | None = None, **kw: Any) -> list[str]:
    result = build(facts, **kw)
    assert result.timeline is None, "expected the build to be refused"
    return [e.message for e in result.errors]


def codes(timeline: BuiltTimeline) -> set[str]:
    return {i.code for i in timeline.issues}


# --- changing a specification (to test what the builder does with it) -------------------------


def change_video(facts: sf.SpecFacts, item_id: str, **changes: Any) -> sf.SpecFacts:
    return replace(
        facts,
        video=tuple(replace(v, **changes) if v.item_id == item_id else v for v in facts.video),
    )


def change_audio(facts: sf.SpecFacts, item_id: str, **changes: Any) -> sf.SpecFacts:
    return replace(
        facts,
        audio=tuple(replace(a, **changes) if a.item_id == item_id else a for a in facts.audio),
    )


def change_overlay(facts: sf.SpecFacts, item_id: str, **changes: Any) -> sf.SpecFacts:
    return replace(
        facts,
        overlays=tuple(
            replace(o, **changes) if o.item_id == item_id else o for o in facts.overlays
        ),
    )


def change_asset(facts: sf.SpecFacts, asset_id: str, **changes: Any) -> sf.SpecFacts:
    return replace(
        facts,
        assets=tuple(replace(a, **changes) if a.asset_id == asset_id else a for a in facts.assets),
    )


def change_transition(facts: sf.SpecFacts, transition_id: str, **changes: Any) -> sf.SpecFacts:
    return replace(
        facts,
        transitions=tuple(
            replace(t, **changes) if t.transition_id == transition_id else t
            for t in facts.transitions
        ),
    )


def change_tracks(facts: sf.SpecFacts, track_id: str, **changes: Any) -> sf.SpecFacts:
    return replace(
        facts,
        tracks=tuple(replace(t, **changes) if t.track_id == track_id else t for t in facts.tracks),
    )


def where(items: Any, test: Callable[[Any], bool]) -> Any:
    return next(i for i in items if test(i))
