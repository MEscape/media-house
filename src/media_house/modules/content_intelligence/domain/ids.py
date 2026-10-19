"""Stable, readable identifiers. Unique within one analysis (``u`` units are in ``units.py``)."""


def segment_id(index: int) -> str:
    return f"s{index + 1:05d}"


def topic_id(index: int) -> str:
    return f"t{index + 1:04d}"


def item_id(index: int) -> str:
    return f"k{index + 1:05d}"


def relationship_id(index: int) -> str:
    return f"r{index + 1:05d}"


def opportunity_id(index: int) -> str:
    return f"o{index + 1:05d}"
