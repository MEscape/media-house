"""Vocabulary of the editorial style: how binding a rule is, where it applies and comes from."""

from enum import StrEnum


class Strength(StrEnum):
    """How binding a rule is. Only a person makes a rule ``MANDATORY``."""

    MANDATORY = "mandatory"
    DEFAULT = "default"
    SUGGESTION = "suggestion"


#: Lower sorts first: a mandatory rule is read before a suggestion.
STRENGTH_RANK: dict[Strength, int] = {
    Strength.MANDATORY: 0,
    Strength.DEFAULT: 1,
    Strength.SUGGESTION: 2,
}


class Scope(StrEnum):
    GLOBAL = "global"
    FORMAT = "format"
    CLIENT = "client"
    PROJECT = "project"


#: The more specific a scope, the later it wins over a general rule of the same strength.
SCOPE_RANK: dict[Scope, int] = {
    Scope.GLOBAL: 0,
    Scope.FORMAT: 1,
    Scope.CLIENT: 2,
    Scope.PROJECT: 3,
}


class Origin(StrEnum):
    SEED = "seed"
    USER = "user"
    LEARNED = "learned"


class Status(StrEnum):
    ACTIVE = "active"
    #: A learned proposal nobody has approved yet: never applied, only shown for review.
    CANDIDATE = "candidate"
    REJECTED = "rejected"


class Category(StrEnum):
    VOICE = "voice"
    HOOK = "hook"
    STRUCTURE = "structure"
    PACING = "pacing"
    GRAPHICS = "graphics"
    TRANSITION = "transition"
    CAPTION = "caption"
    ASSETS = "assets"
    TECHNICAL = "technical"
    HUMOR = "humor"
    NEVER = "never"


class Task(StrEnum):
    """What a generation step is about; decides which rule categories are retrieved for it."""

    CONCEPT = "concept"
    SCRIPT = "script"
    SCENE_REWRITE = "scene_rewrite"
    ASSET_PLAN = "asset_plan"
    CONTEXT = "context"
    METADATA = "metadata"
    EDIT_PLAN = "edit_plan"


TASK_CATEGORIES: dict[Task, frozenset[Category]] = {
    Task.CONCEPT: frozenset(
        {Category.VOICE, Category.HOOK, Category.STRUCTURE, Category.HUMOR, Category.NEVER}
    ),
    Task.SCRIPT: frozenset(
        {
            Category.VOICE,
            Category.HOOK,
            Category.STRUCTURE,
            Category.HUMOR,
            Category.TECHNICAL,
            Category.NEVER,
        }
    ),
    Task.SCENE_REWRITE: frozenset(
        {Category.VOICE, Category.HUMOR, Category.TECHNICAL, Category.NEVER}
    ),
    Task.ASSET_PLAN: frozenset({Category.ASSETS, Category.NEVER}),
    Task.CONTEXT: frozenset(Category),
    Task.METADATA: frozenset({Category.VOICE, Category.NEVER}),
    Task.EDIT_PLAN: frozenset(
        {
            Category.PACING,
            Category.GRAPHICS,
            Category.TRANSITION,
            Category.CAPTION,
            Category.ASSETS,
            Category.NEVER,
        }
    ),
}


class EventKind(StrEnum):
    SUGGESTION_ACCEPTED = "suggestion_accepted"
    SUGGESTION_REJECTED = "suggestion_rejected"
    MANUAL_EDIT = "manual_edit"
    REGENERATE_INSTRUCTION = "regenerate_instruction"
    CONFLICT_OVERRIDE = "conflict_override"
    AB_RATING = "ab_rating"
    FINAL_RATING = "final_rating"
    SCENE_DELETED = "scene_deleted"
    SCENE_ADDED = "scene_added"
    CONCEPT_PICKED = "concept_picked"
