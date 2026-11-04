"""The channel identity's transition language (visual identity v2): approved, prohibited and
brand-bookend-only kinds bound the profile; a v1 style leaves the profile alone."""

from datetime import UTC, datetime

from media_house.modules.transition_engine.application.views import style_limits
from media_house.modules.transition_engine.domain.facts import Bookend
from media_house.modules.transition_engine.domain.values import (
    BookendKind,
    DecisionStatus,
    TransitionKind,
)
from media_house.modules.visual_style_engine.application.contracts import (
    MESC_IDENTITY_V1,
    MESC_IDENTITY_V2,
    StyleConfig,
    resolve_style,
)
from tests.support.trans_fakes import chain, cut, plan, r, sequence, style

K = TransitionKind
TWO = (("c1", "A", 100.0, 110.0), ("c2", "B", 100.0, 110.0))
SECTIONS = {"c1": {"section_id": "s1"}, "c2": {"section_id": "s2"}}
T0 = datetime(2026, 11, 2, tzinfo=UTC)


def test_a_v2_style_hands_its_transition_language_to_the_limits() -> None:
    ranking = resolve_style(MESC_IDENTITY_V2, StyleConfig(profile="ranking"), created_at=T0)
    generic = resolve_style(MESC_IDENTITY_V2, StyleConfig(profile="generic"), created_at=T0)
    old = resolve_style(MESC_IDENTITY_V1, StyleConfig(profile="ranking"), created_at=T0)

    limits = style_limits(ranking, "doc")
    assert {"slide", "whip_pan", "hard_cut"} <= set(limits.approved)
    assert "glitch" in limits.prohibited
    assert set(limits.bookend_only) == {"zoom_tunnel", "pixel_dissolve"}
    assert "whip_pan" not in style_limits(generic, None).approved
    assert style_limits(old, None).approved == ()  # v1: the profile alone decides


def test_the_identity_refuses_what_the_profile_would_use() -> None:
    narrow = style(3, approved=("hard_cut", "cross_dissolve"))
    t = cut(
        plan(sequence(chain(*TWO, **SECTIONS)), profile="ranking", style_limits=narrow), "c1", "c2"
    )

    assert (t.kind, t.status) == (K.HARD_CUT, DecisionStatus.FALLBACK)
    assert [x.kind for x in t.rejected] == [K.SLIDE, K.WHIP_PAN]
    assert all("channel identity" in x.reason for x in t.rejected)


def test_a_prohibited_or_bookend_only_kind_is_refused_inside_the_edit() -> None:
    wide = style(4, approved=("hard_cut", "slide", "whip_pan"), prohibited=("slide",),
                 bookend_only=("whip_pan",))  # fmt: skip
    t = cut(
        plan(sequence(chain(*TWO, **SECTIONS)), profile="ranking", style_limits=wide), "c1", "c2"
    )

    assert t.kind is K.HARD_CUT
    reasons = {x.kind: x.reason for x in t.rejected}
    assert "approved" in reasons[K.SLIDE] and "bookend" in reasons[K.WHIP_PAN]


def test_brand_bookends_stay_the_identity_choice() -> None:
    v2 = style(1, approved=("hard_cut", "zoom_tunnel", "pixel_dissolve"),
               bookend_only=("zoom_tunnel", "pixel_dissolve"))  # fmt: skip
    ends = (
        Bookend(kind=BookendKind.MEMBERSHIP_PLUG, timeline_at=r(10)),
        Bookend(kind=BookendKind.END_SCREEN, timeline_at=r(20)),
    )
    p = plan(sequence(chain(*TWO), bookends=ends), profile="brochure_review", style_limits=v2)

    assert cut(p, "c1", "c2").kind is K.ZOOM_TUNNEL
    assert p.transitions[-1].kind is K.PIXEL_DISSOLVE
