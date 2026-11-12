"""The Resolve timeline plays every sound clip at the level the final mix gives it (a static clip
volume): where Resolve refuses the property, that is a reported gap, never a failed run."""

from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.resolve_adapter.application.views import to_facts
from media_house.modules.resolve_adapter.domain.capabilities import CapabilityMatrix, detect
from media_house.modules.resolve_adapter.domain.config import ResolveAdapterConfig
from media_house.modules.resolve_adapter.domain.facts import TimelineFacts
from media_house.modules.resolve_adapter.domain.translate import translate
from media_house.modules.resolve_adapter.domain.values import (
    Area,
    CapabilityState,
    OperationStatus,
    Verification,
)
from tests.support.resolve_fakes import FakeItem, FakeResolve
from tests.unit.resolve_adapter import fixtures as f
from tests.unit.timeline_builder import fixtures as bf

GAINS = {"a:aV": -2.5, "a:music:seg1": -21.04, "a:sfx:sfx:1": 44.0}
MUSIC = "a:music:seg1"


def facts(gains: dict[str, float] | None = None) -> TimelineFacts:
    return to_facts(bf.built(), name=f.NAME, audio_gains=GAINS if gains is None else gains)


def volume(facts: TimelineFacts, matrix: CapabilityMatrix | None = None) -> dict[str, float]:
    expected = translate(facts, ResolveAdapterConfig(), matrix or detect(None))
    return {i.element_id: dict(i.props)["AudioVolume"] for i in expected.items
            if "AudioVolume" in dict(i.props)}  # fmt: skip


class TestTranslation:
    def test_each_sound_clip_gets_the_mix_level_rounded_and_within_resolves_range(self) -> None:
        assert volume(facts()) == {"a:aV": -2.5, MUSIC: -21.0, "a:sfx:sfx:1": 30.0}

    def test_picture_clips_and_clips_without_a_mix_level_keep_resolves_default(self) -> None:
        assert volume(facts({})) == {}
        assert not any(c.gain_db is not None for c in facts().clips if c.kind == "video")

    def test_fades_and_ducking_stay_with_the_mix_engine(self) -> None:
        expected = translate(facts(), ResolveAdapterConfig(), detect(None))

        gap = next(g for g in expected.gaps if g.element_id == MUSIC)
        assert gap.operation == "audio.gain_fades" and "level is set" in gap.detail

    def test_a_resolve_without_the_capability_reports_the_level_as_a_gap(self) -> None:
        matrix = detect(None)
        matrix = replace(
            matrix,
            items=tuple(
                replace(c, state=CapabilityState.UNSUPPORTED)
                if c.operation == "audio.clip_volume"
                else c
                for c in matrix.items
            ),
        )

        expected = translate(facts(), ResolveAdapterConfig(), matrix)

        assert volume(facts(), matrix) == {}
        assert {g.element_id for g in expected.gaps if g.operation == "audio.clip_volume"} == set(
            GAINS
        )


class TestExecution:
    def test_the_volume_is_set_read_back_and_remembered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(FakeItem, "PROPS", FakeItem.PROPS | {"AudioVolume"})
        ex, fake = f.execution(tmp_path, facts=facts())

        state = f.run(ex)

        audio = [i for lane in fake.projects[f.NAME].timeline.tracks["audio"] for i in lane]
        assert sorted(i.props.get("AudioVolume", 0.0) for i in audio) == sorted(
            [-2.5, -21.0, 30.0, *([0.0] * (len(audio) - 3))]
        )
        assert "audio.clip_volume" in ex.ledger.observed
        assert not any(c.state is Verification.FAILED for c in ex.ledger.checks)
        bound = {e.element_id: dict(e.fields) for e in state.elements}
        assert bound[MUSIC]["prop:AudioVolume"] == "-21"

    def test_a_refused_volume_is_a_gap_not_a_failure(self, tmp_path: Path) -> None:
        ex, _ = f.execution(tmp_path, facts=facts(), fake=FakeResolve())  # the fake refuses it

        state = f.run(ex)

        refused = [o for o in ex.ledger.operations if o.action == "audio.clip_volume"]
        assert {o.element_id for o in refused} == set(GAINS)
        assert all(
            o.status is OperationStatus.UNSUPPORTED and o.area is Area.AUDIO for o in refused
        )
        assert not any(o.status is OperationStatus.FAILED for o in ex.ledger.operations)
        assert not any(c.state is Verification.FAILED for c in ex.ledger.checks)
        assert "audio.clip_volume" not in ex.ledger.observed
        bound = {e.element_id: dict(e.fields) for e in state.elements}
        assert "prop:AudioVolume" not in bound[MUSIC]
