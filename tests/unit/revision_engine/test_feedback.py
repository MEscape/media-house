"""A person's words: what they asked for, about what, and when to ask instead of guessing."""

from media_house.modules.revision_engine.domain.facts import MixView
from media_house.modules.revision_engine.domain.feedback import (
    Context,
    answer,
    drafts_of,
    interpret,
)
from media_house.modules.revision_engine.domain.model import Instruction
from media_house.modules.revision_engine.domain.planner import Draft
from media_house.modules.revision_engine.domain.values import (
    ChangeKind,
    InstructionKind,
    Level,
    TargetKind,
)
from tests.unit.revision_engine.fixtures import PA, PC, timeline

T = timeline()


def one(text: str, **ctx: object) -> Instruction:
    found = interpret(text, "Ada", Context(T, **ctx), feedback_id="f1")  # type: ignore[arg-type]
    assert len(found) == 1, [i.interpretation for i in found]
    return found[0]


def drafts(instruction: Instruction) -> tuple[Draft, ...]:
    return drafts_of(instruction, T, MixView())


def test_the_introduction_is_too_long_is_a_directive_about_the_first_section() -> None:
    instruction = one("The introduction is too long.")

    assert instruction.kind is InstructionKind.DIRECTIVE and instruction.intent == "shorten"
    assert instruction.target.kind is TargetKind.SECTION and instruction.target.section_ids == (
        "s1",
    )
    assert instruction.original == "The introduction is too long."  # the words are kept
    assert instruction.clarification is None and instruction.confidence >= 0.9


def test_make_the_music_less_noticeable_changes_the_music_bus_by_a_small_step() -> None:
    instruction = one("Make the music less noticeable.")

    assert instruction.intent == "quieter" and instruction.target.kind is TargetKind.ROLE
    assert instruction.target.role == "music" and dict(instruction.params)["amount_db"] == "-3.0"
    change = drafts(instruction)[0]
    assert change.kind is ChangeKind.MIX_BUS_GAIN and dict(change.params) == {
        "bus": "music",
        "delta_db": "-3.0",
    }
    assert change.owner == "audio_mix_engine" and change.bus == "music"


def test_a_little_and_a_lot_change_the_size_of_the_step() -> None:
    assert dict(one("Make the music a bit quieter.").params)["amount_db"] == "-1.5"
    assert dict(one("Make the music much quieter.").params)["amount_db"] == "-6.0"
    assert (
        dict(one("The dialogue is too quiet, make it a lot louder.").params)["amount_db"] == "6.0"
    )


def test_use_more_product_close_ups_is_a_general_direction_for_the_broll_owner() -> None:
    instruction = one("Use more product close-ups.")

    assert (
        instruction.intent == "more_of" and dict(instruction.params)["what"] == "product close-ups"
    )
    assert instruction.target.kind is TargetKind.GLOBAL
    change = drafts(instruction)[0]
    assert change.kind is ChangeKind.HANDOFF and change.owner == "broll_intelligence"


def test_this_transition_means_the_one_near_the_playhead() -> None:
    instruction = one("This transition looks distracting.", playhead_frame=105)

    assert instruction.intent == "change_transition"
    assert instruction.target.kind is TargetKind.TRANSITION
    assert instruction.target.transition_ids == ("cut:pA>pB",)
    assert drafts(instruction)[0].owner == "transition_engine"


def test_keep_this_reaction_shot_protects_the_selected_clip_and_changes_nothing() -> None:
    instruction = one("Keep this reaction shot.", selected_clips=(PC,))

    assert instruction.kind is InstructionKind.KEEP
    assert instruction.target.clip_ids == (PC,) and drafts(instruction) == ()


def test_the_captions_are_too_aggressive_goes_to_the_caption_engine() -> None:
    instruction = one("The captions are too aggressive.")

    assert instruction.intent == "softer_style" and instruction.target.role == "captions"
    assert drafts(instruction)[0].owner == "caption_engine"


def test_make_this_section_more_dynamic_uses_the_section_under_the_playhead() -> None:
    instruction = one("Make this section more dynamic.", playhead_frame=130)

    assert instruction.intent == "more_dynamic" and instruction.target.section_ids == ("s2",)
    assert drafts(instruction)[0].owner == "pacing_intelligence"


def test_the_ending_feels_too_abrupt_is_about_the_last_section() -> None:
    instruction = one("The ending feels too abrupt.")

    assert instruction.intent == "abrupt_ending" and instruction.target.section_ids == ("s2",)


def test_leave_this_section_exactly_as_it_is_is_a_lock_not_an_edit() -> None:
    instruction = one("Leave this section exactly as it is.", playhead_frame=20)

    assert instruction.kind is InstructionKind.LOCK and instruction.target.section_ids == ("s1",)
    assert drafts(instruction) == ()


def test_the_dialogue_sounds_too_processed_goes_to_audio_improvement() -> None:
    instruction = one("The dialogue sounds too processed.")

    assert instruction.intent == "less_processed" and instruction.target.role == "dialogue"
    assert drafts(instruction)[0].owner == "audio_improvement"


def test_the_music_should_start_later_and_original_camera_audio_here() -> None:
    later = one("The music should start later.")
    original = one("Use the original camera audio here.", playhead_frame=30)

    assert later.intent == "music_later" and drafts(later)[0].owner == "music_and_sound_design"
    assert original.intent == "original_audio" and original.target.section_ids == ("s1",)
    assert drafts(original)[0].owner == "audio_mix_engine"


def test_music_here_changes_only_the_music_clips_of_this_section() -> None:
    instruction = one("Make the music quieter here.", playhead_frame=30)

    changes = drafts(instruction)

    assert [c.kind for c in changes] == [ChangeKind.MIX_CLIP_GAIN]
    assert changes[0].clip_ids == ("a:music",)


def test_questions_approvals_and_rejections_ask_for_no_change() -> None:
    q = one("Why is the intro so long?")
    ok = one("Looks good!")
    no = one("No, I don't like that.")

    assert (q.kind, ok.kind, no.kind) == (
        InstructionKind.QUESTION,
        InstructionKind.APPROVAL,
        InstructionKind.REJECTION,
    )
    assert drafts(q) == drafts(ok) == drafts(no) == ()


def test_a_polite_request_is_a_directive_not_a_question() -> None:
    instruction = one("Can you make the music quieter?")

    assert instruction.kind is InstructionKind.DIRECTIVE and instruction.intent == "quieter"


def test_a_preference_is_marked_and_kept_below_full_confidence() -> None:
    instruction = one("Maybe a bit shorter intro.")

    assert instruction.kind is InstructionKind.PREFERENCE and instruction.confidence <= 0.7
    assert drafts(instruction)[0].kind is ChangeKind.HANDOFF


def test_two_requests_in_one_sentence_are_two_instructions() -> None:
    found = interpret(
        "Make the music quieter and remove the last transition.",
        "Ada",
        Context(T, playhead_frame=150),
    )

    assert [i.intent for i in found] == ["quieter", "change_transition"] or [
        i.intent for i in found
    ] == ["quieter", "remove"]
    assert len(found) == 2


def test_a_reference_that_could_mean_several_things_is_asked_about_not_guessed() -> None:
    previous = one("Remove the previous shot.")  # no playhead: there is no "previous"
    which = one("Make this section more dynamic.")  # no playhead, no selection
    sound = one("Make it quieter.")

    for instruction in (previous, which, sound):
        assert (
            instruction.target.kind is TargetKind.AMBIGUOUS
            and instruction.clarification is not None
        )
        assert instruction.clarification.options
        assert drafts(instruction) == ()
        assert instruction.confidence < 0.5


def test_answering_the_question_gives_the_same_instruction_its_target() -> None:
    asked = one("Make this section more dynamic.")
    assert asked.clarification is not None
    chosen = asked.clarification.options[1][1]

    settled = answer(asked, chosen)

    assert settled.clarification is None and settled.target.section_ids == ("s2",)
    assert settled.original == asked.original and settled.instruction_id == asked.instruction_id
    assert drafts(settled)[0].owner == "pacing_intelligence"


def test_removing_a_main_shot_is_manual_only_and_a_cutaway_is_only_suggested() -> None:
    main = one("Remove this shot.", selected_clips=(PA,))
    cutaway = one("Remove this clip.", selected_clips=(PC,))

    assert (
        drafts(main)[0].kind is ChangeKind.DELETE_CLIP
        and drafts(main)[0].minimum_level is Level.MANUAL_ONLY
    )
    assert drafts(cutaway)[0].minimum_level is Level.SUGGESTED


def test_words_that_ask_for_nothing_are_not_made_into_something() -> None:
    instruction = one("Hmm, interesting.")

    assert instruction.kind is InstructionKind.CLARIFICATION and drafts(instruction) == ()


def test_the_same_words_give_the_same_instruction() -> None:
    assert one("The introduction is too long.") == one("The introduction is too long.")
