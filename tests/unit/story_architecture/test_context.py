"""The per-video context reaches the story model's prompt as background, and only as that."""

from media_house.modules.story_architecture.domain.refine import with_context


def test_a_context_is_appended_as_background_never_as_a_source_of_facts() -> None:
    system = with_context("Label these sections.", "Werbevideo, fünf Produkte.\nKeine Vergleiche.")

    assert system.startswith("Label these sections.")
    assert "Werbevideo, fünf Produkte." in system
    assert "never add a fact" in system


def test_no_context_leaves_the_prompt_exactly_as_it_was() -> None:
    assert with_context("Label these sections.", "") == "Label these sections."
    assert with_context("Label these sections.", "  \n ") == "Label these sections."
