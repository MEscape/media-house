# ruff: noqa: E501
"""Cue lists per language family. English and German are covered; any other language falls back
to language-neutral signals (punctuation, numbers, repetition, similarity) and a warning.

A cue is evidence with a design-time weight in (0, 1]: how strongly it suggests its function when
it appears at the START of a sentence (``lead``) or anywhere in it (``any``). Weights are not
probabilities. Cue lists are part of ``PROCESSING_VERSION``: edit one, bump the version.
"""

import re
from dataclasses import dataclass
from functools import cache, lru_cache
from typing import Final

SUPPORTED: Final = frozenset({"en", "de"})


@dataclass(frozen=True, slots=True)
class Cue:
    name: str
    pattern: re.Pattern[str]
    weight: float


def _cues(entries: dict[str, tuple[float, str]]) -> tuple[Cue, ...]:
    return tuple(
        Cue(name, re.compile(pattern, re.IGNORECASE | re.UNICODE), weight)
        for name, (weight, pattern) in entries.items()
    )


def _lead(*phrases: str) -> str:
    return r"^\W*(?:" + "|".join(phrases) + r")\b"


def _any(*phrases: str) -> str:
    return r"\b(?:" + "|".join(phrases) + r")\b"


# Families: each maps a cue name to (weight, regex). ``lead_*`` match at sentence start.
_EN: Final = {
    "question_word": (0.5, _lead("what", "why", "how", "when", "where", "who", "which", "is it", "do you", "can you", "does", "are", "should")),
    "answer_lead": (0.6, _lead("yes", "no", "because", "the reason", "the answer", "that's because", "it's because", "so the")),
    "definition": (0.7, _any(r"is defined as", r"means that", r"refers to", r"is called", r"stands for", r"we call", r"is what we call", r"in other words")),
    "example": (0.8, _any(r"for example", r"for instance", r"such as", r"imagine", r"take .{1,30} for example", r"say you", r"let's say", r"e\.g\.")),
    "demonstration": (0.7, _any(r"let me show", r"i'll show", r"as you can see", r"you can see", r"here you", r"watch", r"look at", r"now i'm going to", r"let's try")),
    "explanation": (0.5, _any(r"because", r"which means", r"that's why", r"the reason", r"so that", r"works by", r"this is how", r"due to")),
    "evidence": (0.6, _any(r"according to", r"studies show", r"research", r"data shows", r"statistics", r"percent", r"measured", r"tested", r"survey")),
    "caveat": (0.8, _lead("but", "however", "that said", "although", "though", "except", "unless", "only if", "of course", "to be fair", "keep in mind", "note that", "the catch")),
    "hedge": (0.5, _any(r"maybe", r"perhaps", r"probably", r"might", r"could be", r"i think", r"i guess", r"not sure", r"seems", r"roughly", r"about", r"sort of", r"kind of")),
    "counter": (0.7, _any(r"on the other hand", r"some people say", r"critics", r"but others", r"the opposite", r"however")),
    "transition": (0.8, _lead("next", "now", "moving on", "let's move", "let's talk about", "let's look at", "so now", "okay so", "alright", "on to", "another thing", "second", "finally", "the next")),
    "recap": (0.9, _any(r"to summari[sz]e", r"in summary", r"in short", r"to recap", r"so far", r"as we saw", r"to sum up", r"the takeaway", r"in conclusion")),
    "callback": (0.7, _any(r"as i said", r"as i mentioned", r"like i said", r"remember when", r"earlier", r"back to", r"as we discussed")),
    "conclusion": (0.8, _any(r"in conclusion", r"to conclude", r"the bottom line", r"the takeaway", r"all in all", r"so overall", r"that's it", r"in the end", r"ultimately")),
    "intro": (0.8, _any(r"welcome", r"today we", r"today i", r"in this video", r"in this episode", r"i'm going to", r"my name is", r"we're going to", r"let's get started", r"hi everyone", r"hello everyone")),
    "context": (0.6, _any(r"some background", r"before we", r"first of all", r"to start with", r"for context", r"background", r"let me explain")),
    "decision": (0.8, _any(r"we decided", r"i decided", r"we chose", r"the decision", r"we'll go with", r"i'm going with", r"we agreed", r"we will", r"let's go with")),
    "recommendation": (0.8, _any(r"i recommend", r"you should", r"you shouldn't", r"i'd suggest", r"my advice", r"make sure", r"don't forget", r"always", r"never", r"best way", r"try to")),
    "action": (0.8, _any(r"we need to", r"you need to", r"someone has to", r"to do", r"action item", r"by (?:monday|tuesday|wednesday|thursday|friday|tomorrow|next week)", r"follow up", r"i'll send", r"i will send", r"please send")),
    "call_to_action": (0.9, _any(r"subscribe", r"click", r"link below", r"sign up", r"check out", r"leave a comment", r"follow me", r"download", r"visit", r"join")),
    "anecdote": (0.7, _any(r"i remember", r"once", r"a few years ago", r"back then", r"when i was", r"one time", r"last year", r"the other day", r"story")),
    "turning": (0.7, _any(r"but then", r"suddenly", r"that's when", r"everything changed", r"turns out", r"it turned out", r"until")),
    "resolution": (0.7, _any(r"finally", r"in the end", r"it worked", r"solved", r"fixed", r"figured out", r"problem was")),
    "emphasis": (0.7, _any(r"really important", r"very important", r"the key", r"crucial", r"critical", r"essential", r"remember this", r"pay attention", r"most important", r"keep in mind", r"big deal")),
    "surprise": (0.6, _any(r"surprising", r"surprisingly", r"shocking", r"unexpected", r"believe it or not", r"you won't believe", r"turns out", r"wow", r"incredible", r"myth")),
    "personal": (0.6, _any(r"i struggled", r"i felt", r"my own", r"personally", r"i was afraid", r"i admit", r"honestly", r"to be honest", r"i lost", r"my family")),
    "humor": (0.7, _any(r"just kidding", r"joke", r"funny", r"hilarious", r"haha", r"lol")),
    "correction": (0.8, _any(r"i was wrong", r"correction", r"actually,? no", r"no wait", r"scratch that", r"i misspoke", r"let me correct", r"that's not (?:quite )?right")),
    "contrast": (0.5, _any(r"instead", r"rather than", r"as opposed to", r"unlike", r"whereas", r"compared (?:to|with)", r"versus", r"while the")),
    "continuation": (0.5, _lead("and", "also", "plus", "furthermore", "moreover", "in addition", "then", "so", "which")),
    "persuade": (0.5, _any(r"you deserve", r"imagine if", r"what if", r"don't miss", r"you can't afford", r"the best", r"everyone")),
    "process": (0.6, _any(r"step", r"first,? you", r"then you", r"after that", r"next,? you", r"finally,? you", r"process", r"procedure", r"how it works")),
    "location": (0.5, _any(r"in (?:the )?(?:city|town|village|country|kitchen|office|garden|forest|desert|mountains?|lab|factory|store|studio)", r"located", r"over there", r"at the (?:station|airport|beach|park|site)")),
    "history": (0.6, _any(r"in (?:1[0-9]{3}|20[0-2][0-9])", r"history", r"historically", r"decades? ago", r"centuries?", r"was founded", r"invented", r"war", r"ancient")),
    "comparison": (0.6, _any(r"compared (?:to|with)", r"versus", r"vs\.?", r"difference between", r"better than", r"worse than", r"bigger than", r"smaller than", r"faster than", r"cheaper than")),
    "object": (0.4, _any(r"this (?:device|tool|machine|product|camera|phone|part|piece)", r"the (?:device|tool|machine|product|camera|phone|part)", r"hold(?:ing)? (?:up|this)")),
    "list": (0.6, _any(r"first(?:ly)?,", r"second(?:ly)?,", r"third(?:ly)?,", r"three (?:things|ways|steps|reasons)", r"two (?:things|ways|steps|reasons)", r"four (?:things|ways|steps|reasons)", r"five (?:things|ways|steps|reasons)", r"number (?:one|two|three)", r"the following")),
    "quote": (0.6, _any(r"he said", r"she said", r"they said", r"quote", r"as .{1,25} put it", r"in the words of")),
    "name_role": (0.7, _any(r"my name is", r"i'm [A-Z][a-z]+ and i", r"i am [A-Z][a-z]+", r"joining us (?:is|today)", r"our guest", r"i work as", r"i'm the")),
    "unfamiliar_term": (0.4, _any(r"so[- ]called", r"known as", r"technically called", r"what's called", r"the term")),
    "filler": (0.9, r"^\W*(?:um+|uh+|er+|hmm+|you know|like|so yeah|yeah|okay|ok)\W*$"),
}  # fmt: skip

_DE: Final = {
    "question_word": (0.5, _lead("was", "warum", "wieso", "weshalb", "wie", "wann", "wo", "wer", "welche[rsmn]?", "ist es", "kann man", "soll", "sollte", "gibt es")),
    "answer_lead": (0.6, _lead("ja", "nein", "weil", "der grund", "die antwort", "das liegt daran", "also")),
    "definition": (0.7, _any(r"bedeutet", r"bezeichnet", r"definiert als", r"nennt man", r"heißt", r"steht für", r"mit anderen worten", r"das heißt")),
    "example": (0.8, _any(r"zum beispiel", r"beispielsweise", r"etwa", r"stell dir vor", r"stellt euch vor", r"nehmen wir", r"sagen wir", r"z\.\s?b\.")),
    "demonstration": (0.7, _any(r"ich zeige", r"lass mich zeigen", r"wie ihr seht", r"wie du siehst", r"hier siehst", r"schau", r"schaut", r"jetzt probieren", r"gucken wir")),
    "explanation": (0.5, _any(r"weil", r"denn", r"das bedeutet", r"deshalb", r"deswegen", r"der grund", r"damit", r"funktioniert", r"so funktioniert", r"aufgrund")),
    "evidence": (0.6, _any(r"laut", r"studien", r"forschung", r"daten zeigen", r"statistik", r"prozent", r"gemessen", r"getestet", r"umfrage")),
    "caveat": (0.8, _lead("aber", "allerdings", "jedoch", "trotzdem", "obwohl", "außer", "es sei denn", "nur wenn", "natürlich", "fairerweise", "beachte", "beachtet", "der haken", "wichtig ist aber")),
    "hedge": (0.5, _any(r"vielleicht", r"eventuell", r"wahrscheinlich", r"könnte", r"ich glaube", r"ich denke", r"ich schätze", r"nicht sicher", r"scheint", r"ungefähr", r"etwa", r"irgendwie")),
    "counter": (0.7, _any(r"andererseits", r"manche sagen", r"kritiker", r"aber andere", r"das gegenteil", r"jedoch")),
    "transition": (0.8, _lead("als nächstes", "nun", "weiter geht", "kommen wir", "lass uns", "lasst uns", "so jetzt", "okay", "gut", "zum nächsten", "ein weiterer punkt", "zweitens", "schließlich", "außerdem")),
    "recap": (0.9, _any(r"zusammengefasst", r"zusammenfassend", r"kurz gesagt", r"wie gesagt", r"bisher", r"wie wir gesehen haben", r"unterm strich", r"fazit")),
    "callback": (0.7, _any(r"wie gesagt", r"wie erwähnt", r"wie ich vorhin sagte", r"erinnert ihr euch", r"zurück zu", r"wie besprochen", r"vorhin")),
    "conclusion": (0.8, _any(r"fazit", r"zusammenfassend", r"unterm strich", r"letztendlich", r"am ende", r"insgesamt", r"alles in allem", r"abschließend")),
    "intro": (0.8, _any(r"willkommen", r"heute", r"in diesem video", r"in dieser folge", r"ich werde", r"mein name ist", r"wir werden", r"los geht", r"hallo zusammen", r"hallo und")),
    "context": (0.6, _any(r"zum hintergrund", r"bevor wir", r"zuerst", r"zunächst", r"zum kontext", r"hintergrund", r"lass mich erklären")),
    "decision": (0.8, _any(r"wir haben entschieden", r"ich habe entschieden", r"wir haben uns", r"die entscheidung", r"wir nehmen", r"wir gehen mit", r"wir haben vereinbart", r"wir werden")),
    "recommendation": (0.8, _any(r"ich empfehle", r"du solltest", r"ihr solltet", r"man sollte", r"mein rat", r"achte darauf", r"vergiss nicht", r"vergesst nicht", r"immer", r"niemals", r"am besten", r"versuche")),
    "action": (0.8, _any(r"wir müssen", r"du musst", r"ihr müsst", r"jemand muss", r"aufgabe", r"bis (?:montag|dienstag|mittwoch|donnerstag|freitag|morgen|nächste woche)", r"nachfassen", r"ich schicke", r"bitte schick")),
    "call_to_action": (0.9, _any(r"abonniert", r"abonnieren", r"klick", r"link in der beschreibung", r"anmelden", r"schaut euch", r"schreibt .{0,20}kommentar", r"folgt mir", r"herunterladen", r"besucht")),
    "anecdote": (0.7, _any(r"ich erinnere mich", r"einmal", r"vor ein paar jahren", r"damals", r"als ich", r"neulich", r"letztes jahr", r"geschichte")),
    "turning": (0.7, _any(r"aber dann", r"plötzlich", r"da war es", r"alles änderte sich", r"es stellte sich heraus", r"bis")),
    "resolution": (0.7, _any(r"schließlich", r"am ende", r"es hat funktioniert", r"gelöst", r"behoben", r"herausgefunden", r"das problem war")),
    "emphasis": (0.7, _any(r"sehr wichtig", r"wirklich wichtig", r"der schlüssel", r"entscheidend", r"kritisch", r"wesentlich", r"merkt euch", r"merk dir", r"aufpassen", r"am wichtigsten", r"wichtig")),
    "surprise": (0.6, _any(r"überraschend", r"überraschenderweise", r"schockierend", r"unerwartet", r"ihr werdet nicht glauben", r"wow", r"unglaublich", r"mythos")),
    "personal": (0.6, _any(r"ich habe gekämpft", r"ich fühlte", r"persönlich", r"ich hatte angst", r"ich gebe zu", r"ehrlich gesagt", r"ehrlich", r"meine familie")),
    "humor": (0.7, _any(r"nur spaß", r"witz", r"lustig", r"urkomisch", r"haha")),
    "correction": (0.8, _any(r"ich habe mich geirrt", r"korrektur", r"nein warte", r"streich das", r"ich habe mich versprochen", r"das stimmt nicht", r"ich korrigiere")),
    "contrast": (0.5, _any(r"stattdessen", r"anstatt", r"im gegensatz", r"während", r"verglichen mit", r"gegenüber")),
    "continuation": (0.5, _lead("und", "auch", "außerdem", "zudem", "darüber hinaus", "dann", "also", "was")),
    "persuade": (0.5, _any(r"du verdienst", r"stell dir vor", r"was wäre wenn", r"verpass nicht", r"das beste", r"jeder")),
    "process": (0.6, _any(r"schritt", r"zuerst musst", r"dann musst", r"danach", r"als nächstes musst", r"prozess", r"vorgehen", r"so funktioniert")),
    "location": (0.5, _any(r"in (?:der|dem|die) (?:stadt|küche|büro|garten|wald|wüste|berge|labor|fabrik|laden|studio)", r"befindet sich", r"dort drüben", r"am (?:bahnhof|flughafen|strand|park)")),
    "history": (0.6, _any(r"im jahr (?:1[0-9]{3}|20[0-2][0-9])", r"in (?:1[0-9]{3}|20[0-2][0-9])", r"geschichte", r"historisch", r"vor jahrzehnten", r"jahrhunderte", r"gegründet", r"erfunden", r"krieg", r"antike")),
    "comparison": (0.6, _any(r"verglichen mit", r"im vergleich", r"gegenüber", r"unterschied zwischen", r"besser als", r"schlechter als", r"größer als", r"kleiner als", r"schneller als", r"billiger als")),
    "object": (0.4, _any(r"dieses (?:gerät|werkzeug|produkt|teil|handy)", r"diese (?:kamera|maschine)", r"dieser (?:apparat)", r"das (?:gerät|werkzeug|produkt|teil)")),
    "list": (0.6, _any(r"erstens", r"zweitens", r"drittens", r"drei (?:dinge|wege|schritte|gründe)", r"zwei (?:dinge|wege|schritte|gründe)", r"vier (?:dinge|wege|schritte|gründe)", r"fünf (?:dinge|wege|schritte|gründe)", r"nummer (?:eins|zwei|drei)", r"folgende")),
    "quote": (0.6, _any(r"er sagte", r"sie sagte", r"sie sagten", r"zitat", r"wie .{1,25} es ausdrückte", r"in den worten von")),
    "name_role": (0.7, _any(r"mein name ist", r"ich bin [A-ZÄÖÜ][a-zäöüß]+ und ich", r"zu gast ist", r"unser gast", r"ich arbeite als", r"ich bin der")),
    "unfamiliar_term": (0.4, _any(r"sogenannt", r"bekannt als", r"technisch genannt", r"der begriff")),
    "filler": (0.9, r"^\W*(?:äh+|ähm+|öh+|hm+|na ja|ja gut|also ja|okay|ok|genau)\W*$"),
}  # fmt: skip

_PRONOUN_LEAD_EN: Final = _lead(
    "this",
    "that",
    "these",
    "those",
    "it",
    "they",
    "he",
    "she",
    "its",
    "such",
    "the latter",
    "the former",
    "there",
    "then",
    "so",
    "and",
    "but",
    "which",
)
_PRONOUN_LEAD_DE: Final = _lead(
    "das",
    "dies",
    "dieses",
    "diese",
    "dieser",
    "es",
    "sie",
    "er",
    "dabei",
    "dafür",
    "dadurch",
    "damit",
    "deshalb",
    "dann",
    "also",
    "und",
    "aber",
    "was",
    "dort",
    "so",
)

_STOP_EN: Final = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "by", "for", "with", "from", "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "this", "that", "these", "those", "i", "you", "he", "she", "we", "they", "me", "my", "your", "his", "her", "our", "their", "there", "here", "not", "no", "do", "does", "did", "have", "has", "had", "will", "would", "can", "could", "should", "may", "might", "so", "than", "then", "too", "very", "just", "also", "about", "into", "over", "after", "before", "up", "down", "out", "off", "than", "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "all", "any", "each", "more", "most", "other", "some", "such", "only", "own", "same", "s", "t"]
)  # fmt: skip
_STOP_DE: Final = frozenset(
    ["der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines", "und", "oder", "aber", "wenn", "weil", "als", "ob", "zu", "in", "im", "am", "an", "auf", "aus", "bei", "mit", "nach", "von", "vor", "für", "über", "unter", "durch", "um", "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich", "sich", "mein", "dein", "sein", "ihr", "unser", "euer", "ist", "sind", "war", "waren", "sein", "bin", "bist", "habe", "hat", "haben", "hatte", "wird", "werden", "wurde", "kann", "können", "muss", "müssen", "soll", "sollen", "nicht", "kein", "keine", "auch", "noch", "nur", "schon", "sehr", "so", "dann", "da", "hier", "dort", "mal", "wie", "was", "wer", "wo", "wann", "warum", "dass", "zum", "zur", "vom", "beim", "ins", "ans"]
)  # fmt: skip

_PRONOUNS: Final = {
    "en": frozenset({"this", "that", "these", "those", "it", "they", "he", "she", "its", "such", "there"}),
    "de": frozenset({"das", "dies", "dieses", "diese", "dieser", "es", "sie", "er", "dabei", "dafür", "dadurch", "damit", "deshalb", "dort"}),
}  # fmt: skip

_FILLER_TOKENS: Final = {
    "en": frozenset(
        {"um", "uh", "er", "hmm", "like", "basically", "literally", "actually", "yeah"}
    ),
    "de": frozenset({"äh", "ähm", "öh", "hm", "halt", "eigentlich", "quasi", "irgendwie", "genau"}),
}


@lru_cache(maxsize=262_144)
def _weight(cue: Cue, text: str) -> float:
    """Pure and memoised: the same cue on the same sentence is asked for many times."""
    return cue.weight if cue.pattern.search(text) else 0.0


@dataclass(frozen=True, slots=True)
class Lexicon:
    """The cues and word lists of one language family (empty for unsupported languages)."""

    family: str | None
    supported: bool
    cues: dict[str, Cue]
    anaphora: re.Pattern[str] | None
    stopwords: frozenset[str]
    fillers: frozenset[str]
    #: Words that, opening a passage, point back at something said before.
    pronouns: frozenset[str] = frozenset()

    def weight(self, name: str, text: str) -> float:
        """The cue's weight if it matches ``text`` (0.0 when it does not or has no entry)."""
        cue = self.cues.get(name)
        return _weight(cue, text) if cue is not None else 0.0

    def matches(self, text: str, names: tuple[str, ...] | None = None) -> dict[str, float]:
        selected = names if names is not None else tuple(self.cues)
        return {n: c.weight for n in selected if (c := self.cues.get(n)) and c.pattern.search(text)}


def lexicon_for(language: str | None) -> Lexicon:
    """The lexicon of a language tag (``de``, ``de-AT``, ``pt_BR``); one object per family."""
    return _lexicon(language.split("-")[0].split("_")[0].lower() if language else None)


@cache
def _lexicon(family: str | None) -> Lexicon:
    if family == "en":
        return Lexicon(
            "en", True, {c.name: c for c in _cues(_EN)}, re.compile(_PRONOUN_LEAD_EN, re.I),
            _STOP_EN, _FILLER_TOKENS["en"], _PRONOUNS["en"],
        )  # fmt: skip
    if family == "de":
        return Lexicon(
            "de", True, {c.name: c for c in _cues(_DE)}, re.compile(_PRONOUN_LEAD_DE, re.I),
            _STOP_DE, _FILLER_TOKENS["de"], _PRONOUNS["de"],
        )  # fmt: skip
    return Lexicon(family, False, {}, None, frozenset(), frozenset())
