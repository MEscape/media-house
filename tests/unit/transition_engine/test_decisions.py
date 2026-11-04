"""The decision: a hard cut unless a transition adds meaning, within ceiling, budget and fit."""

from media_house.modules.transition_engine.domain.facts import (
    Bookend,
    TransitionRequest,
    TransitionSequence,
)
from media_house.modules.transition_engine.domain.profile import (
    JsonValue,
    SectionOverride,
    TransitionDecision,
)
from media_house.modules.transition_engine.domain.values import (
    AudioKind,
    BookendKind,
    Context,
    DecisionAction,
    DecisionStatus,
    TransitionKind,
)
from tests.support.trans_fakes import (
    FPS,
    chain,
    codes,
    cut,
    edge,
    plan,
    r,
    sequence,
    source,
    style,
    words,
)

K = TransitionKind
TWO = (("c1", "A", 100.0, 110.0), ("c2", "B", 100.0, 110.0))
SECTIONS = {"c1": {"section_id": "s1"}, "c2": {"section_id": "s2"}}


def test_adjacent_clips_get_a_hard_cut_by_default() -> None:
    p = plan(sequence(chain(*TWO)))
    t = cut(p, "c1", "c2")
    assert (t.kind, t.status, t.intensity, t.duration_frames) == (
        K.HARD_CUT,
        DecisionStatus.NOT_NEEDED,
        0,
        0,
    )
    assert t.overlay is None
    assert not t.required
    assert t.reasons
    assert p.events == ()
    assert [x.boundary.value for x in p.transitions] == ["sequence_start", "cut", "sequence_end"]


def test_continuous_material_is_an_invisible_cut_without_an_audio_edit() -> None:
    p = plan(sequence(chain(("c1", "A", 100.0, 110.0), ("c2", "A", 110.0, 120.0))))
    t = cut(p, "c1", "c2")
    assert Context.CONTINUOUS in t.contexts
    assert t.kind is K.HARD_CUT
    assert t.audio.kind is AudioKind.NONE


def test_another_camera_angle_does_not_receive_an_effect() -> None:
    edges = {("c1", "out"): edge(scene_id="sc1"), ("c2", "in"): edge("s2", scene_id="sc1")}
    p = plan(sequence(chain(*TWO, **SECTIONS)), profile="modern_youtube", edges=edges)
    t = cut(p, "c1", "c2")
    assert Context.ANGLE_CHANGE in t.contexts
    # a SECTION change still marks: the angle alone never does
    p2 = plan(sequence(chain(*TWO)), profile="modern_youtube", edges=edges)
    assert cut(p2, "c1", "c2").kind is K.HARD_CUT


def test_a_section_change_receives_the_profile_transition() -> None:
    p = plan(sequence(chain(*TWO, **SECTIONS)), profile="modern_youtube")
    t = cut(p, "c1", "c2")
    assert (t.kind, t.status, t.direction) == (K.PUSH, DecisionStatus.RECOMMENDED, "in")
    assert t.duration_frames == 12  # 0.4 s at 30 fps
    assert (t.start_frame, t.cut_frame, t.end_frame) == (294, 300, 306)
    assert [(e.kind, e.intensity) for e in p.events] == [(K.PUSH, 2)]
    assert t.audio.kind is AudioKind.CROSSFADE  # follows the picture (0.4 s)
    clean = cut(plan(sequence(chain(*TWO, **SECTIONS))), "c1", "c2")
    assert clean.kind is K.HARD_CUT
    assert "section change" in clean.reasons[0]


def test_an_editorial_request_is_honoured_as_requested() -> None:
    req = TransitionRequest(request_id="fx1", timeline_at=r(10))
    p = plan(sequence(chain(*TWO), requests=(req,)))
    t = cut(p, "c1", "c2")
    assert (t.kind, t.status) == (K.CROSS_DISSOLVE, DecisionStatus.REQUESTED)
    assert (t.duration_frames, t.handles.required_out, t.handles.required_in) == (15, 8, 7)


def test_scene_and_location_changes_are_contextual() -> None:
    edges = {("c1", "out"): edge(scene_id="sc1"), ("c2", "in"): edge("s2", scene_id="sc2")}
    doc = cut(plan(sequence(chain(*TWO)), profile="documentary", edges=edges), "c1", "c2")
    assert (doc.kind, doc.duration_frames) == (K.CROSS_DISSOLVE, 30)
    ranking = cut(plan(sequence(chain(*TWO)), profile="ranking", edges=edges), "c1", "c2")
    assert ranking.kind is K.HARD_CUT  # the channel cuts between locations


def test_a_product_reveal_gets_a_reveal_transition() -> None:
    clips = chain(
        *TWO, c1={"beat_id": "b1", "beat_function": "demonstration"},
        c2={"beat_id": "b2", "beat_function": "payoff"},
    )  # fmt: skip
    t = cut(plan(sequence(clips), profile="product_review"), "c1", "c2")
    assert Context.REVEAL in t.contexts
    assert t.kind is K.ZOOM


def test_the_style_ceiling_bounds_the_intensity() -> None:
    p = plan(sequence(chain(*TWO, **SECTIONS)), profile="ranking", style_limits=style(1))
    t = cut(p, "c1", "c2")
    assert (t.kind, t.status) == (K.HARD_CUT, DecisionStatus.FALLBACK)
    assert t.fallback is not None
    assert t.fallback.wanted is K.SLIDE
    assert "above the ceiling 1" in t.fallback.reason
    assert [x.kind for x in t.rejected] == [K.SLIDE, K.WHIP_PAN]


def test_budget_and_spacing_keep_transitions_rare() -> None:
    specs = [(f"c{i}", "A" if i % 2 else "B", 100.0, 110.0) for i in range(1, 7)]
    per = {f"c{i}": {"section_id": f"s{i}"} for i in range(1, 7)}
    p = plan(sequence(chain(*specs, **per)), profile="ranking")
    kinds = [cut(p, f"c{i}", f"c{i + 1}").kind for i in range(1, 6)]
    # 60 s at 1.5/min = 2 transitions, at least 20 s apart
    assert kinds == [K.SLIDE, K.HARD_CUT, K.HARD_CUT, K.SLIDE, K.HARD_CUT]
    assert "after the previous transition" in cut(p, "c2", "c3").rejected[0].reason
    assert "budget" in cut(p, "c5", "c6").rejected[0].reason


def test_expressive_transitions_are_never_consecutive() -> None:
    specs = [(f"c{i}", "A" if i % 2 else "B", 100.0, 110.0) for i in range(1, 4)]
    per = {f"c{i}": {"section_id": f"s{i}"} for i in range(1, 4)}
    overrides: dict[str, JsonValue] = {
        "budget.max_intensity": 3,
        "budget.per_minute": 30.0,
        "budget.min_spacing": 0.0,
        "budget.expressive_spacing": 0.0,
        "budget.max_expressive": 10,
        "contexts.section_change": ["flash"],
    }
    p = plan(sequence(chain(*specs, **per)), overrides=overrides)
    assert cut(p, "c1", "c2").kind is K.FLASH
    second = cut(p, "c2", "c3")
    assert second.kind is K.HARD_CUT
    assert "expressive" in second.rejected[0].reason


def test_repeated_and_consecutive_flashy_decisions_are_reported() -> None:
    specs = [(f"c{i}", "A" if i % 2 else "B", 100.0, 110.0) for i in range(1, 7)]
    decisions = tuple(
        TransitionDecision(f"cut:c{i}>c{i + 1}", DecisionAction.USE, K.ZOOM_TUNNEL)
        for i in range(1, 6)
    )
    p = plan(sequence(chain(*specs)), decisions=decisions)
    assert all(cut(p, f"c{i}", f"c{i + 1}").kind is K.ZOOM_TUNNEL for i in range(1, 6))
    assert {"repetition.same_kind", "repetition.consecutive_expressive"} <= codes(p)


def test_motion_transitions_need_compatible_camera_motion() -> None:
    seq = sequence(chain(*TWO, **SECTIONS))
    none = cut(plan(seq, profile="shopping_tour"), "c1", "c2")
    assert none.kind is K.HARD_CUT
    assert "no picture analysis" in none.rejected[0].reason
    pans = {
        ("c1", "out"): edge(camera_movement="pan", camera_direction=0.0),
        ("c2", "in"): edge("s2", camera_movement="pan", camera_direction=10.0),
    }
    whip = cut(plan(seq, profile="shopping_tour", edges=pans), "c1", "c2")
    assert (whip.kind, whip.direction) == (K.WHIP_PAN, "right")
    against = {**pans, ("c2", "in"): edge("s2", camera_movement="pan", camera_direction=180.0)}
    refused = cut(plan(seq, profile="shopping_tour", edges=against), "c1", "c2")
    assert refused.kind is K.HARD_CUT
    assert "different directions" in refused.rejected[0].reason


def test_composition_conflicts_are_detected() -> None:
    texted = {("c2", "in"): edge("s2", has_text=True)}
    t = cut(
        plan(sequence(chain(*TWO, **SECTIONS)), profile="modern_youtube", edges=texted), "c1", "c2"
    )
    assert t.kind is K.CROSS_DISSOLVE  # the push would move the on-screen text
    assert "text" in t.rejected[0].reason
    clips = chain(*TWO, c2={"beat_id": "b2", "beat_function": "payoff"})
    apart = {
        ("c1", "out"): edge(subject_cx=0.2, subject_cy=0.5),
        ("c2", "in"): edge("s2", subject_cx=0.8, subject_cy=0.5),
    }
    z = cut(plan(sequence(clips), profile="product_review", edges=apart), "c1", "c2")
    assert z.kind is K.HARD_CUT
    assert "compositions differ" in z.rejected[0].reason


def test_a_dissolve_that_would_ghost_falls_back_to_a_cut() -> None:
    edges = {
        ("c1", "out"): edge(scene_id="sc1", subject_vx=0.5, subject_vy=0.0),
        ("c2", "in"): edge("s2", scene_id="sc2", subject_vx=-0.5, subject_vy=0.0),
    }
    t = cut(plan(sequence(chain(*TWO)), profile="documentary", edges=edges), "c1", "c2")
    assert (t.kind, t.status) == (K.HARD_CUT, DecisionStatus.FALLBACK)
    assert "ghost" in t.rejected[0].reason


def test_brand_bookends_follow_the_identity() -> None:
    ends = (
        Bookend(kind=BookendKind.MEMBERSHIP_PLUG, timeline_at=r(10)),
        Bookend(kind=BookendKind.END_SCREEN, timeline_at=r(20)),
    )
    p = plan(sequence(chain(*TWO), bookends=ends), profile="brochure_review", style_limits=style(1))
    plug = cut(p, "c1", "c2")
    assert (plug.kind, plug.status) == (K.ZOOM_TUNNEL, DecisionStatus.BRAND)
    assert plug.overlay is not None
    assert (plug.overlay.overlay, plug.overlay.request_id) == ("zoom_tunnel", "tx:cut:c1>c2")
    assert plug.handles.required_out == plug.handles.required_in == 0  # a cover, no handles
    end = p.transitions[-1]
    assert (end.kind, end.status, end.cut_frame) == (K.PIXEL_DISSOLVE, DecisionStatus.BRAND, 600)
    assert end.overlay is not None
    assert end.start_frame < 600 < end.end_frame
    assert [e.kind for e in p.events] == [K.ZOOM_TUNNEL, K.PIXEL_DISSOLVE]


def test_person_decisions_win_and_are_kept_in_the_plan() -> None:
    use = TransitionDecision("cut:c1>c2", DecisionAction.USE, K.DIP_TO_BLACK, note="calm")
    p = plan(sequence(chain(*TWO)), decisions=(use,))
    t = cut(p, "c1", "c2")
    assert (t.kind, t.status, t.duration_frames, t.decision_note) == (
        K.DIP_TO_BLACK,
        DecisionStatus.OVERRIDDEN,
        18,
        "calm",
    )
    assert p.decisions == (use,)
    keep = TransitionDecision("cut:c1>c2", DecisionAction.HARD_CUT)
    hard = cut(
        plan(sequence(chain(*TWO, **SECTIONS)), profile="modern_youtube", decisions=(keep,)),
        "c1",
        "c2",
    )
    assert (hard.kind, hard.status) == (K.HARD_CUT, DecisionStatus.OVERRIDDEN)


def test_a_section_can_be_restricted_to_cuts() -> None:
    off = SectionOverride("s2", {"contexts.section_change": [], "contexts.requested": []})
    t = cut(
        plan(sequence(chain(*TWO, **SECTIONS)), profile="modern_youtube", section_overrides=(off,)),
        "c1",
        "c2",
    )
    assert (t.kind, t.status) == (K.HARD_CUT, DecisionStatus.NOT_NEEDED)


def test_no_speech_one_clip_has_only_its_bookends() -> None:
    seq = TransitionSequence.no_speech((("A", r(60)),), frame_rate=FPS)
    p = plan(seq, profile=None, sources={"A": source("A", 60.0)})
    assert p.profile == "clean_default"
    assert [t.kind for t in p.transitions] == [K.HARD_CUT, K.HARD_CUT]
    assert (p.transitions[0].audio.kind, p.transitions[1].audio.kind) == (
        AudioKind.FADE_IN,
        AudioKind.FADE_OUT,
    )
    assert p.events == ()
    meme = plan(seq, profile="shorts_meme", sources={"A": source("A", 60.0)})
    assert {t.audio.kind for t in meme.transitions} == {AudioKind.NONE}  # the Short loops
    ended = TransitionSequence.no_speech(
        (("A", r(60)),),
        frame_rate=FPS,
        bookends=(Bookend(kind=BookendKind.END_SCREEN, timeline_at=r(60)),),
    )
    uncut = plan(ended, profile="nye_uncut", sources={"A": source("A", 60.0)})
    assert uncut.transitions[-1].kind is K.PIXEL_DISSOLVE


def test_a_sentence_across_the_cut_is_never_dipped_to_black() -> None:
    sources = {
        "A": source("A", words=words((109.8, 109.99))),
        "B": source("B", words=words((100.05, 100.4))),
    }
    p = plan(sequence(chain(*TWO, **SECTIONS)), profile="documentary", sources=sources)
    t = cut(p, "c1", "c2")
    assert Context.SPEECH_ACROSS in t.contexts
    assert t.kind is K.CROSS_DISSOLVE
    assert t.rejected[0].kind is K.DIP_TO_BLACK


def test_a_bookend_off_the_cuts_is_reported() -> None:
    ends = (Bookend(kind=BookendKind.MEMBERSHIP_PLUG, timeline_at=r(5)),)
    p = plan(sequence(chain(*TWO), bookends=ends), profile="ranking")
    assert "bookend.not_at_cut" in codes(p)
