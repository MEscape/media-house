"""Attempt transitions and the FFmpeg command a spec produces."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.proxy_manager.application.ports import EncodeRequest
from media_house.modules.proxy_manager.domain.attempt import (
    TRANSITIONS,
    Attempt,
    AttemptState,
    FailureKind,
)
from media_house.modules.proxy_manager.domain.profile import resolve_profile
from media_house.modules.proxy_manager.domain.source import SourceFacts
from media_house.modules.proxy_manager.domain.spec import resolve_spec
from media_house.modules.proxy_manager.domain.values import JsonValue
from media_house.modules.proxy_manager.infrastructure.ffmpeg_encoder import build_encode_args
from media_house.modules.proxy_manager.infrastructure.migrations import MIGRATIONS
from media_house.shared.errors import InvariantViolation
from tests.support.proxy_fakes import audio_facts, source_facts, video_facts

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def attempt(**changes: Any) -> Attempt:
    base = Attempt(
        id="a1",
        source_asset_id="s",
        profile="edit",
        profile_version=1,
        spec_fingerprint="f" * 64,
        owner_token="t",
        created_at=NOW,
    )
    return replace(base, **changes)


def test_the_happy_path_walks_every_stage_to_ready() -> None:
    current = attempt()
    for state in (
        AttemptState.GENERATING,
        AttemptState.VALIDATING,
        AttemptState.PUBLISHING,
        AttemptState.READY,
    ):
        current = current.advance(state, now=NOW)

    assert current.state.terminal and current.finished_at == NOW


@pytest.mark.parametrize(
    ("start", "to"),
    [
        (AttemptState.QUEUED, AttemptState.READY),
        (AttemptState.GENERATING, AttemptState.PUBLISHING),
        (AttemptState.VALIDATING, AttemptState.READY),
        (AttemptState.READY, AttemptState.GENERATING),
        (AttemptState.FAILED, AttemptState.QUEUED),
        (AttemptState.SUPERSEDED, AttemptState.READY),
    ],
)
def test_an_illegal_transition_is_a_bug_and_raises(start: AttemptState, to: AttemptState) -> None:
    with pytest.raises(InvariantViolation):
        attempt(state=start).advance(to, now=NOW)


def test_every_live_state_can_fail_be_cancelled_or_be_interrupted() -> None:
    for state in (AttemptState.QUEUED, AttemptState.GENERATING, AttemptState.PUBLISHING):
        assert (
            attempt(state=state).fail(FailureKind.TRANSIENT, "x", now=NOW).state
            is AttemptState.FAILED
        )
        assert (
            attempt(state=state).fail(FailureKind.CANCELLED, "x", now=NOW).state
            is AttemptState.CANCELLED
        )


def test_reuse_and_adoption_skip_encoding() -> None:
    assert (AttemptState.QUEUED, AttemptState.PUBLISHING) in TRANSITIONS
    assert (AttemptState.QUEUED, AttemptState.VALIDATING) in TRANSITIONS


def test_the_sql_transition_table_equals_the_domain_table() -> None:
    seed = next(
        s for s in MIGRATIONS[0].statements if s.startswith("INSERT INTO attempt_transitions")
    )
    pairs = {
        (a, b)
        for a, b in (
            chunk.strip(" ()'").split("', '") for chunk in seed.split("VALUES ")[1].split("), (")
        )
    }

    assert pairs == {(a.value, b.value) for a, b in TRANSITIONS}


# ------------------------------------------------------------------------------ ffmpeg args
def request(
    profile: str, facts: SourceFacts | None = None, **overrides: JsonValue
) -> EncodeRequest:
    facts = facts or source_facts()
    spec = resolve_spec(resolve_profile(profile, overrides), facts)
    return EncodeRequest(Path("in file.mp4"), Path("out.mp4"), spec, facts, 123_456)


def test_the_command_is_an_argument_list_with_paths_passed_verbatim() -> None:
    args = build_encode_args(request("analysis"))

    assert "in file.mp4" in args and args[-1] == "out.mp4"
    assert all(isinstance(a, str) for a in args)


def test_h264_command_promises_size_timing_and_a_size_cap() -> None:
    args = build_encode_args(request("analysis"))

    assert args[args.index("-vf") : args.index("-vf") + 2] == [
        "-vf",
        "scale=640:360:flags=bicubic,setsar=1",
    ]
    assert args[args.index("-fps_mode") + 1] == "passthrough"
    assert args[args.index("-c:v") + 1] == "libx264" and "+faststart" in args
    assert args[args.index("-fs") + 1] == "123456"
    assert args[args.index("-map_chapters") + 1] == "-1"


def test_prores_command_uses_the_proxy_profile_in_mov() -> None:
    args = build_encode_args(request("edit"))

    assert args[args.index("-c:v") + 1] == "prores_ks" and args[args.index("-profile:v") + 1] == "0"
    assert "yuv422p10le" in args and "+faststart" not in args and args[-1] == "out.mp4"
    assert args[args.index("-c:a") + 1] == "pcm_s16le"


def test_rotation_is_kept_as_metadata_via_an_input_option_before_the_input() -> None:
    facts = source_facts(video_facts(rotation=90))

    args = build_encode_args(request("analysis", facts))

    assert args.index("-noautorotate") < args.index("-i")
    assert "-noautorotate" not in build_encode_args(request("analysis"))


def test_only_the_mapped_streams_are_selected() -> None:
    two = source_facts(video_facts(index=3), [audio_facts(index=5), audio_facts(index=6)])

    first = build_encode_args(request("analysis", two))
    everything = build_encode_args(request("edit", two))

    def maps(args: list[str]) -> list[str]:
        return [args[i + 1] for i, x in enumerate(args) if x == "-map"]

    assert maps(first) == ["0:3", "0:5"] and maps(everything) == ["0:3", "0:5", "0:6"]


def test_a_source_without_audio_disables_audio() -> None:
    assert "-an" in build_encode_args(request("analysis", source_facts(audio=[])))


def test_each_mapped_stream_gets_its_own_tags_copied_explicitly() -> None:
    two = source_facts(video_facts(index=3), [audio_facts(index=5), audio_facts(index=6)])

    args = build_encode_args(request("edit", two))

    pairs = {args[i]: args[i + 1] for i, a in enumerate(args) if a.startswith("-map_metadata:s:")}
    assert pairs == {
        "-map_metadata:s:0": "0:s:3",
        "-map_metadata:s:1": "0:s:5",
        "-map_metadata:s:2": "0:s:6",
    }
