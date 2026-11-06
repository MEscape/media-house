"""The shared strict JSON codec: exact, strict, deterministic (used by three documents)."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from fractions import Fraction

import pytest

from media_house.core.domain import Rational, json_codec


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


@dataclass(frozen=True, slots=True)
class Leaf:
    at: Rational
    pair: tuple[str, int]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Root:
    name: str
    color: Color
    leaves: tuple[Leaf, ...]
    when: datetime
    ratio: float
    flag: bool = False


def sample() -> Root:
    return Root(
        "x",
        Color.BLUE,
        (Leaf(Rational(1, 3), ("a", 1)), Leaf(Rational(2, 1), ("b", 2), "n")),
        datetime(2026, 11, 6, 9, 0, tzinfo=UTC),
        0.5,
    )


def body() -> dict[str, object]:
    out: dict[str, object] = json.loads(json_codec.dumps("root", sample()))
    del out["document_type"]
    return out


def test_it_reads_back_equal_with_exact_times_never_floats() -> None:
    text = json_codec.dumps("root", sample())

    assert json.loads(text)["leaves"][0]["at"] == {"numerator": 1, "denominator": 3}
    assert json_codec.decode(Root, body()) == sample()


def test_the_text_is_deterministic_and_names_its_type() -> None:
    assert json_codec.dumps("root", sample()) == json_codec.dumps("root", sample())
    assert json.loads(json_codec.dumps("root", sample()))["document_type"] == "root"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(surprise=1), "unknown fields"),
        (lambda d: d.pop("name"), "missing field name"),
        (lambda d: d.update(name=3), "expected str"),
        (lambda d: d.update(color="green"), "unknown Color"),
        (lambda d: d.update(leaves="no"), "expected a list"),
        (lambda d: d.update(when=5), "expected a timestamp"),
        (lambda d: d.update(flag=1), "expected bool"),
    ],
)
def test_a_document_that_is_not_exactly_the_shape_is_refused(mutate, message: str) -> None:  # type: ignore[no-untyped-def]
    document = body()
    mutate(document)

    with pytest.raises((TypeError, ValueError), match=message):
        json_codec.decode(Root, document)


def test_a_fixed_size_tuple_must_have_that_size() -> None:
    document = body()
    document["leaves"] = [{"at": {"numerator": 1, "denominator": 1}, "pair": ["a"]}]

    with pytest.raises(ValueError, match="expected 2 items"):
        json_codec.decode(Root, document)


def test_null_is_only_allowed_where_the_type_says_so() -> None:
    document = body()
    document["name"] = None

    with pytest.raises(TypeError):
        json_codec.decode(Root, document)


def test_an_int_is_accepted_as_a_float_but_a_bool_is_not_an_int() -> None:
    document = body()
    document["ratio"] = 1
    assert json_codec.decode(Root, document).ratio == 1.0

    leaf = {"at": {"numerator": True, "denominator": 1}, "pair": ["a", 1]}
    document["leaves"] = [leaf]
    with pytest.raises(TypeError):
        json_codec.decode(Root, document)


def test_digests_are_stable_order_free_for_sets_and_sensitive_to_content() -> None:
    assert json_codec.digest({"b": 1, "a": 2}) == json_codec.digest({"a": 2, "b": 1})
    assert json_codec.digest(frozenset({"x", "y"})) == json_codec.digest(frozenset({"y", "x"}))
    assert json_codec.digest(Fraction(1, 3)) == json_codec.digest(Fraction(2, 6))
    assert json_codec.digest(sample()) != json_codec.digest(Leaf(Rational(1, 3), ("a", 1)))
    assert len(json_codec.digest(sample())) == 64
