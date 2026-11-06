"""The capability matrix: a method that exists is not a capability that works."""

from media_house.modules.resolve_adapter.domain.capabilities import REGISTRY, detect, spec_of
from media_house.modules.resolve_adapter.domain.environment import (
    ResolveEnvironment,
    ResolveVersion,
)
from media_house.modules.resolve_adapter.domain.values import CapabilityState as S


def env(
    *,
    edition: str = "studio",
    version: tuple[int, ...] = (19, 0, 3),
    methods: tuple[str, ...] = (
        "Resolve.GetProjectManager",
        "MediaPool.AppendToTimeline",
        "MediaPool.ImportMedia",
    ),
    probed: tuple[str, ...] = ("Resolve", "MediaPool"),
) -> ResolveEnvironment:
    return ResolveEnvironment(
        product="DaVinci Resolve Studio" if edition == "studio" else "DaVinci Resolve",
        version=ResolveVersion.parse(list(version)),
        edition=edition,
        operating_system="win32",
        scripting_module="x",
        probed=probed,
        methods=methods,
    )


class TestRegistry:
    def test_every_operation_says_how_it_is_verified(self) -> None:
        assert all(s.verification for s in REGISTRY)

    def test_operation_ids_are_unique(self) -> None:
        assert len({s.operation for s in REGISTRY}) == len(REGISTRY)

    def test_every_known_gap_names_what_could_replace_it_or_who_owns_it(self) -> None:
        gaps = [s for s in REGISTRY if s.known_gap]

        assert {g.operation for g in gaps} >= {
            "transition.insert",
            "clip.retime",
            "effect.native",
            "clip.crop_keyframed",
            "audio.gain_fades",
        }
        assert all(g.alternatives or g.owner for g in gaps)


class TestStates:
    def test_a_method_that_exists_is_not_verified_until_something_confirms_it(self) -> None:
        matrix = detect(env())

        assert matrix.state("clip.place") is S.NOT_VERIFIED
        assert matrix.usable("clip.place")

    def test_a_read_back_confirms_it_on_this_resolve(self) -> None:
        matrix = detect(env(), frozenset({"clip.place"}))

        assert matrix.state("clip.place") is S.SUPPORTED

    def test_a_confirmed_operation_with_a_documented_limit_is_only_partially_supported(
        self,
    ) -> None:
        matrix = detect(env(), frozenset({"clip.crop_static"}))

        assert matrix.state("clip.crop_static") is S.PARTIALLY_SUPPORTED

    def test_a_known_gap_stays_unsupported_even_when_everything_else_works(self) -> None:
        matrix = detect(env(), frozenset(s.operation for s in REGISTRY))

        assert matrix.state("transition.insert") is S.UNSUPPORTED
        assert not matrix.usable("transition.insert")
        assert matrix.get("transition.insert").alternatives

    def test_a_missing_method_of_a_probed_class_is_unsupported(self) -> None:
        matrix = detect(env(methods=("MediaPool.ImportMedia",)))

        assert matrix.state("clip.place") is S.UNSUPPORTED
        assert "AppendToTimeline" in matrix.get("clip.place").reason

    def test_a_class_that_was_not_reachable_yet_is_not_judged(self) -> None:
        matrix = detect(env(probed=("Resolve",), methods=()))

        assert matrix.state("clip.place") is S.NOT_VERIFIED

    def test_an_older_resolve_makes_an_operation_version_dependent(self) -> None:
        matrix = detect(env(version=(16, 2)))

        assert matrix.state("item.identity") is S.VERSION_DEPENDENT

    def test_the_free_edition_makes_external_scripting_edition_dependent(self) -> None:
        assert (
            detect(env(edition="free")).state("connection.external_scripting")
            is S.EDITION_DEPENDENT
        )

    def test_a_connection_that_works_settles_the_edition_question(self) -> None:
        matrix = detect(env(edition="free"), frozenset({"connection.external_scripting"}))

        assert matrix.state("connection.external_scripting") is S.SUPPORTED

    def test_no_connection_means_nothing_was_checked(self) -> None:
        matrix = detect(None)

        assert matrix.state("clip.place") is S.NOT_VERIFIED
        assert matrix.state("clip.retime") is S.UNSUPPORTED  # a gap is a gap everywhere

    def test_unsupported_lists_every_operation_that_cannot_be_attempted(self) -> None:
        names = {c.operation for c in detect(env(edition="free")).unsupported()}

        assert {"transition.insert", "connection.external_scripting"} <= names
        assert "clip.place" not in names

    def test_unknown_operations_are_an_error_not_a_guess(self) -> None:
        import pytest

        with pytest.raises(KeyError):
            detect(env()).state("nope")
        assert spec_of("clip.place").methods


class TestVersion:
    def test_it_reads_the_resolve_version_list(self) -> None:
        v = ResolveVersion.parse([19, 0, 3, 2, "b"])

        assert v is not None and v.text == "19.0.3.2b" and v.at_least((19, 0))

    def test_a_short_list_is_padded_and_nonsense_is_refused(self) -> None:
        assert ResolveVersion.parse([18]) == ResolveVersion(18, 0, 0, 0, "")
        assert ResolveVersion.parse([]) is None
        assert ResolveVersion.parse(["x"]) is None
        assert ResolveVersion.parse([True]) is None

    def test_older_is_not_at_least(self) -> None:
        assert not ResolveVersion(17, 4).at_least((18, 0))
