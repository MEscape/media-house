"""The planner: music per section, restraint, preservation, licences, missing assets, mix intent."""

import itertools
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.music_and_sound_design.domain.library import (
    LicencePolicy,
    SoundAsset,
    SoundIndex,
)
from media_house.modules.music_and_sound_design.domain.model import ItemDecision, SoundDesignPlan
from media_house.modules.music_and_sound_design.domain.planner import (
    PlanInputs,
    PlanMeta,
    cache_key,
    plan_sound_design,
    role_of,
)
from media_house.modules.music_and_sound_design.domain.profile import (
    SectionOverride,
    SoundDesignConfig,
)
from media_house.modules.music_and_sound_design.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.music_and_sound_design.domain.timeline import (
    AudioBridge,
    OriginalSoundEvent,
    PacingCue,
    SoundtrackTimeline,
)
from media_house.modules.music_and_sound_design.domain.validation import validate_plan
from media_house.modules.music_and_sound_design.domain.values import (
    Alignment,
    BridgeKind,
    ClipAudio,
    CrossfadeKind,
    Decision,
    Declined,
    DuckKey,
    EventSource,
    LicenceState,
    MusicMode,
    Need,
    OriginalAudioMode,
    SectionRole,
    SoundKind,
)
from tests.support.fakes import T0
from tests.support.sound_fakes import clip, event, music, r, section, sfx, speech, timeline


def library(*extra: SoundAsset) -> SoundIndex:
    return SoundIndex(
        [
            music(
                "hype", ("hype_energetic",), bpm=128, onset_rate=7, lufs=-10, credit='"Hype" by A'
            ),
            music("calm", ("upbeat_positive", "chill_lofi"), bpm=90, onset_rate=2, lufs=-20),
            sfx("boom", ("impact",)),
            sfx("swish", ("whoosh", "swish_swipe"), duration=1.0, peak_at=0.6),
            sfx("pop1", ("pop",)),
            sfx("pop2", ("pop",)),
            *extra,
        ]
    )


def plan(
    soundtrack: SoundtrackTimeline,
    config: SoundDesignConfig | None = None,
    index: SoundIndex | None = None,
    decisions: tuple[ItemDecision, ...] = (),
) -> SoundDesignPlan:
    config = config or SoundDesignConfig()
    inputs = PlanInputs(timeline=soundtrack, index=index or library(), decisions=decisions)
    result = plan_sound_design(
        inputs, config, config.resolve(soundtrack.story_format), now=T0, meta=PlanMeta()
    )
    assert validate_plan(result) == []
    return result


def seconds(value: Rational) -> float:
    return float(Fraction(value.numerator, value.denominator))


def ranking() -> SoundtrackTimeline:
    return timeline(
        duration=90.0,
        sections=(
            section("s1", 0, 10, "hook"),
            section("s2", 10, 40, "explanation", "explanation"),
            section("s3", 40, 60, "demonstration"),
            section("s4", 60, 90, "payoff"),
        ),
        clips=(clip("c1", 0, 40), clip("c2", 40, 60), clip("c3", 60, 90)),
        speech_spans=(speech(1, 8), speech(12, 20), speech(21, 35), speech(62, 70)),
    )


def test_roles_come_from_beat_functions() -> None:
    assert role_of(("hook",)) is SectionRole.HOOK
    assert role_of(("explanation", "payoff", "explanation")) is SectionRole.EXPLANATION
    assert role_of(()) is SectionRole.BODY
    assert role_of(("call_to_action",)) is SectionRole.CALL_TO_ACTION


def test_music_follows_sections_and_never_covers_the_fireworks_test() -> None:
    p = plan(ranking(), SoundDesignConfig(profile="ranking"))

    enabled = [c for c in p.music if c.mode is MusicMode.MUSIC_ENABLED]
    assert [c.section_ids for c in enabled] == [("s1",), ("s2",), ("s4",)]
    free = [(seconds(f.timeline_start), seconds(f.timeline_end), f.mode) for f in p.music_free]
    assert (40.0, 60.0, MusicMode.MUSIC_DISABLED) in free
    assert all(
        seconds(s.timeline_end) <= 40 or seconds(s.timeline_start) >= 60
        for c in enabled
        for s in c.segments
    )
    modes = {o.clip_id: o.mode for o in p.original_audio}
    assert modes["c2"] is OriginalAudioMode.DOMINANT
    assert enabled[0].asset is not None and enabled[0].asset.asset_id == "hype"


def test_music_starts_on_downbeats_and_the_last_cue_ends_with_the_track() -> None:
    p = plan(ranking(), SoundDesignConfig(profile="ranking"))

    hook, _, last = (c for c in p.music if c.segments)
    assert hook.segments[0].on_downbeat and seconds(hook.segments[0].source_start) == 0.0
    assert last.alignment is Alignment.BACK_TIMED
    assert seconds(last.segments[-1].source_end) == 120.0  # the track's measured ending
    assert seconds(last.segments[-1].fade_out) == 0.0
    for cue in p.music:
        for s in cue.segments:
            assert s.timeline_start_frame == round(
                Fraction(s.timeline_start.numerator, s.timeline_start.denominator) * 30
            )


def test_touching_cues_get_a_tail_crossfade_and_follow_a_j_cut() -> None:
    soundtrack = timeline(
        duration=60.0,
        sections=(section("a", 0, 30, "hook"), section("b", 30, 60, "explanation")),
        bridges=(
            AudioBridge(
                bridge_id="j1",
                kind=BridgeKind.J_CUT,
                timeline_audio_at=r(29),
                timeline_picture_at=r(30),
            ),
        ),
    )

    p = plan(soundtrack)

    first, second = (c for c in p.music if c.segments)
    assert seconds(first.timeline_end) == 29.0 == seconds(second.timeline_start)
    assert seconds(first.segments[-1].timeline_end) == 30.0  # 1 s tail under the new cue
    xf = p.crossfades[0]
    assert xf.kind is CrossfadeKind.TAIL and xf.follows_bridge == "j1"
    assert xf.incoming_cue_id == second.cue_id


def test_a_short_track_loops_its_measured_region() -> None:
    index = SoundIndex([music("short", ("upbeat_positive",), duration=50.0, loop=(8.0, 40.0))])
    soundtrack = timeline(duration=200.0, sections=(section("a", 0, 200, "context"),))

    p = plan(soundtrack, SoundDesignConfig(overrides={"music.natural_ending": False}), index)

    cue = p.music[0]
    assert cue.alignment is Alignment.LOOPED
    assert len(cue.segments) > 2
    loop_start = seconds(cue.segments[1].source_start)
    assert abs(loop_start - 8.0) < 0.012  # the downbeat frame nearest 8 s
    assert all(seconds(s.source_start) == loop_start for s in cue.segments[1:])
    assert seconds(cue.segments[-1].timeline_end) == 200.0
    assert sum(seconds(s.timeline_end) - seconds(s.timeline_start) for s in cue.segments) == 200.0


def test_ducking_under_speech_and_loud_original_moments() -> None:
    soundtrack = timeline(
        duration=40.0,
        sections=(section("a", 0, 40, "context"),),
        speech_spans=(speech(2, 5), speech(5.5, 9)),
        original_events=(
            OriginalSoundEvent(
                clip_id="c1", kind="energy_spike", timeline_start=r(20), timeline_end=r(20.5),
                strength=0.9,
            ),
        ),
    )  # fmt: skip

    p = plan(soundtrack)

    speech_ducks = [d for d in p.ducking if d.key is DuckKey.SPEECH]
    assert [(seconds(d.timeline_start), seconds(d.timeline_end)) for d in speech_ducks] == [
        (2.0, 9.0)  # the 0.5 s pause is bridged: no pumping
    ]
    assert speech_ducks[0].depth_db == 8.0 and speech_ducks[0].attack_seconds == 0.2
    assert [d.key for d in p.ducking if d.key is DuckKey.ORIGINAL_AUDIO] == [DuckKey.ORIGINAL_AUDIO]


def test_restraint_no_sound_per_cut_weak_caption_or_crowded_moment() -> None:
    soundtrack = timeline(
        duration=60.0,
        events=(
            event("cut", 5, "cut", source=EventSource.TRANSITION),
            event("emoji", 10, "emoji", source=EventSource.CAPTION, strength=0.5),
            event("zoom", 20, "zoom", source=EventSource.TRANSITION, strength=0.9),
            event("pop", 20.5, "pop_in", strength=0.85),
            event("mystery", 30, "sparkle"),
        ),
    )

    p = plan(soundtrack)

    assert [e.sync_event_id for e in p.sfx] == ["zoom"]
    declined = {n.sync_event_id: n.declined for n in p.no_sfx}
    assert declined == {
        "cut": Declined.NO_RULE,
        "emoji": Declined.TOO_WEAK,
        "pop": Declined.TOO_CLOSE,
        "mystery": Declined.NO_RULE,
    }
    zoom = p.sfx[0]
    assert seconds(zoom.timeline_start) == 19.4  # its measured peak lands on the event
    assert zoom.asset.asset_id == "swish"


def test_the_per_minute_budget_and_the_repeat_window_are_kept() -> None:
    events = tuple(event(f"p{i}", 2.0 + 2 * i, "pop_in") for i in range(12))
    config = SoundDesignConfig(
        overrides={
            "sfx.max_per_minute": 4.0,
            "sfx.min_spacing_seconds": 1.0,
            "sfx.repeat_window": 1,  # two pops in the library: they alternate
        }
    )

    p = plan(timeline(duration=60.0, events=events), config)

    assert len(p.sfx) == 4
    assert {n.declined for n in p.no_sfx} == {Declined.DENSITY}
    used = [e.asset.asset_id for e in p.sfx]
    assert all(a != b for a, b in itertools.pairwise(used))


def test_effects_never_mask_speech_or_cover_preserved_audio() -> None:
    soundtrack = timeline(
        duration=40.0,
        sections=(section("talk", 0, 20, "explanation"), section("test", 20, 40, "demonstration")),
        speech_spans=(speech(2, 10),),
        clips=(clip("c1", 0, 20), clip("c2", 20, 40)),
        events=(event("rank", 5, "rank_reveal"), event("bang", 30, "rank_reveal")),
    )

    p = plan(soundtrack)

    declined = {n.sync_event_id: n.declined for n in p.no_sfx}
    assert declined == {"rank": Declined.MASKS_SPEECH, "bang": Declined.PRESERVED_AUDIO}


def test_a_missing_sound_is_recommended_never_substituted() -> None:
    soundtrack = timeline(
        duration=30.0,
        sections=(section("a", 0, 30, "hook"),),
        events=(event("reveal", 10, "reveal"), event("reveal2", 20, "reveal")),
    )
    index = SoundIndex([sfx("boom", ("impact",))])  # no riser, no music at all

    p = plan(soundtrack, index=index)

    assert p.sfx == ()
    assert {n.declined for n in p.no_sfx} == {Declined.NO_ASSET}
    kinds = {(m.kind, m.priority) for m in p.missing_assets}
    assert (SoundKind.SFX, Need.RECOMMENDED) in kinds
    assert (SoundKind.MUSIC, Need.RECOMMENDED) in kinds  # the hook wanted featured music
    assert len([m for m in p.missing_assets if m.kind is SoundKind.SFX]) == 1  # one per purpose
    assert all(m.action == "manual_asset_acquisition" for m in p.missing_assets)
    assert p.music[0].mode is MusicMode.MUSIC_DISABLED and p.music[0].segments == ()


def test_only_commercially_cleared_music_is_chosen_by_default() -> None:
    index = SoundIndex([music("unsure", ("upbeat_positive",), commercial=None)])
    soundtrack = timeline(duration=30.0, sections=(section("a", 0, 30, "context"),))

    default = plan(soundtrack, index=index)
    allowed = plan(
        soundtrack, SoundDesignConfig(licence=LicencePolicy(allow_unconfirmed=True)), index
    )

    assert default.music[0].segments == ()
    assert allowed.music[0].asset is not None
    assert allowed.music[0].asset.licence_state is LicenceState.UNCONFIRMED
    assert "licence_unconfirmed" in {c.code for c in allowed.checks}


def test_credits_carry_the_exact_attribution_text_once() -> None:
    p = plan(ranking(), SoundDesignConfig(profile="ranking"))

    assert [c.text for c in p.credits] == ['"Hype" by A']
    assert p.credit_lines == '"Hype" by A'


def test_person_decisions_win_and_survive_in_the_plan() -> None:
    soundtrack = timeline(
        duration=40.0,
        sections=(section("a", 0, 40, "context"),),
        events=(event("zoom", 20, "zoom", source=EventSource.TRANSITION),),
    )
    decisions = (
        ItemDecision("mus:a", Decision.ACCEPTED, asset_id="hype"),
        ItemDecision("sfx:transition:zoom", Decision.REJECTED, note="too much"),
    )

    p = plan(soundtrack, decisions=decisions)

    assert p.music[0].asset is not None and p.music[0].asset.asset_id == "hype"
    assert p.music[0].decision is Decision.ACCEPTED
    assert p.sfx == ()
    assert p.no_sfx[0].declined is Declined.PERSON_REJECTED and p.no_sfx[0].reason == "too much"
    assert p.decisions == tuple(sorted(decisions, key=lambda d: d.item_id))


def test_section_override_makes_a_deliberate_silence() -> None:
    config = SoundDesignConfig(
        section_overrides=(SectionOverride("b", {"music.presence.explanation": "silence"}),)
    )
    soundtrack = timeline(
        duration=60.0,
        sections=(section("a", 0, 30, "context"), section("b", 30, 60, "explanation")),
    )

    p = plan(soundtrack, config)

    silences = [f for f in p.music_free if f.mode is MusicMode.INTENTIONAL_SILENCE]
    assert [(seconds(f.timeline_start), f.section_id) for f in silences] == [(30.0, "b")]


def test_music_drops_out_before_a_held_reveal() -> None:
    soundtrack = timeline(
        duration=60.0,
        sections=(section("a", 0, 60, "context"),),
        pacing=(
            PacingCue(cue_id="hr1", kind="hold_reveal", timeline_start=r(30), timeline_end=r(32)),
        ),
    )

    p = plan(soundtrack, SoundDesignConfig(profile="ranking"))

    spans = [(seconds(s.timeline_start), seconds(s.timeline_end)) for s in p.music[0].segments]
    assert (29.5, 30.0) not in spans
    assert any(end == 29.5 for _, end in spans) and any(start == 30.0 for start, _ in spans)
    assert any(f.mode is MusicMode.INTENTIONAL_SILENCE for f in p.music_free)


def test_no_speech_path_plans_by_format_and_never_fails() -> None:
    bare = timeline(duration=30.0, edit=None, clips=(clip("rec", 0.0, 30.0),))

    nye = plan(bare, SoundDesignConfig(profile="nye_uncut"), index=library())
    meme = plan(
        bare,
        SoundDesignConfig(
            profile="shorts_meme", overrides={"music.categories.hook": ["hype_energetic"]}
        ),
    )
    generic = plan(bare)

    assert nye.music == () and nye.sfx == ()
    assert [o.mode for o in nye.original_audio] == [OriginalAudioMode.DOMINANT]
    assert meme.music[0].role is SectionRole.HOOK and meme.music[0].segments
    assert generic.music[0].segments and generic.ducking == ()
    assert generic.edit is None and generic.project_id is None


def test_muted_and_voiceover_clips_are_recorded_as_such() -> None:
    soundtrack = timeline(
        duration=20.0,
        clips=(
            clip("m", 0, 10, audio=ClipAudio.MUTED),
            clip("v", 10, 20, audio=ClipAudio.VOICEOVER),
        ),
    )

    p = plan(soundtrack)

    assert [o.mode for o in p.original_audio] == [
        OriginalAudioMode.MUTED_BY_EDIT,
        OriginalAudioMode.VOICEOVER,
    ]
    assert [(seconds(d.timeline_start), seconds(d.timeline_end)) for d in p.ducking] == [
        (10.0, 20.0)
    ]


def test_plans_are_deterministic_round_trip_and_keyed_by_their_inputs() -> None:
    first, second = plan(ranking()), plan(ranking())
    inputs = PlanInputs(timeline=ranking(), index=library())
    config = SoundDesignConfig()
    other = PlanInputs(timeline=ranking(), index=library(sfx("new", ("pop",))))

    assert first.plan_hash == second.plan_hash and first.plan_id == second.plan_id
    assert plan_from_json(plan_to_json(first)) == first
    assert cache_key(inputs, config, config.resolve()) != cache_key(other, config, config.resolve())


def test_a_few_shouts_over_fireworks_keep_the_take_sound_in_front() -> None:
    # E2E regression (NYE uncut, 85 s): six shouted words made the whole take a "speech" clip.
    from tests.support.sound_fakes import speech

    shouts = timeline(
        duration=85.0, edit=None, clips=(clip("rec", 0.0, 85.0),),
        speech_spans=(speech(0.3, 2.5), speech(53.9, 54.5)),
    )  # fmt: skip
    talk = timeline(
        duration=85.0, edit=None, clips=(clip("rec", 0.0, 85.0),), speech_spans=(speech(0, 60),)
    )
    config = SoundDesignConfig(profile="nye_uncut")

    assert [o.mode for o in plan(shouts, config).original_audio] == [OriginalAudioMode.DOMINANT]
    assert [o.mode for o in plan(talk, config).original_audio] == [OriginalAudioMode.SPEECH]


def test_the_appended_end_screen_gets_the_outro_and_no_speech_ducking() -> None:
    # E2E O14: the end screen is appended after the content (it never covers it).
    from media_house.core.domain import Rational
    from tests.support.sound_fakes import speech

    soundtrack = timeline(
        duration=60.0, speech_spans=(speech(0, 60),), appended_until=Rational(80, 1)
    )

    p = plan(soundtrack, SoundDesignConfig(profile="ranking"))

    assert seconds(p.timeline_duration) == 80.0
    [outro] = [c for c in p.music if c.section_ids == ("tail",)]
    assert outro.role is SectionRole.OUTRO
    assert all(seconds(d.timeline_end) <= 60 for d in p.ducking if d.reason == "speech")
