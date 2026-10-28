import logging

import pytest

from lull.stages.plan import (
    MAX_WORDS,
    PLAN_SCHEMA,
    PlanResult,
    budget,
    count_words,
    load_prompt,
    plan,
)
from lull.stages.research import GlossaryEntry, ResearchResult
from lull.tf import ULTRA
from lull.types import Gap, RunOptions, VisualEvent, Word


class FakeTF:
    """Stands in for TokenFactory: returns canned JSON per call and records every call."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def json(self, model, messages, schema, **kw) -> dict:
        self.calls.append({"model": model, "messages": messages, "schema": schema, **kw})
        # once the scripted answers run out: an answer with no items (the plan asks again for
        # salient events it got no item for)
        return self.responses.pop(0) if self.responses else {"cast": [], "items": []}


def _ev(id_, t0, t1, kind="action", text="A person opens a door.", *, sal=3, verb=None, said=False):
    return VisualEvent(
        id=id_, t_start=t0, t_end=t1, kind=kind, text=text,
        verbatim_text=verb, salience=sal, said_in_dialogue=said,
    )  # fmt: skip


def _gap(id_, us, ue, cap):
    return Gap(id=id_, start=us - 0.2, end=ue + 0.2, usable_start=us, usable_end=ue,
               capacity_words=cap)  # fmt: skip


def _item(event_id, priority=3, short="A door opens.", medium=None, long=None):
    return {"event_id": event_id, "priority": priority, "short": short,
            "medium": medium, "long": long}  # fmt: skip


EVENTS = [
    _ev("e001", 2.0, 6.0, "slide", "The slide is titled Water use.", sal=5,
        verb="Water use by sector\n2.1 million litres"),
    _ev("e002", 8.0, 9.0, "gesture", "The presenter points at the slide.", sal=4, said=True),
    _ev("e003", 10.0, 14.0, "person", "A bald person in a dark coat stands on a gantry.",
        sal=5, said=True),
    _ev("e004", 20.0, 22.0, "setting", "A small office with a whiteboard.", sal=2),
]  # fmt: skip
WORDS = [
    Word(text="Hello", start=61.0, end=61.4, speaker=1),
    Word(text="there.", start=61.5, end=62.0, speaker=1),
    Word(text="Proog,", start=63.0, end=63.4, speaker=2),
    Word(text="wait!", start=63.5, end=63.9, speaker=2),
]
GAPS = [_gap("g001", 2.2, 5.6, 10), _gap("g002", 15.0, 19.4, 13)]
RESEARCH = ResearchResult(
    glossary=[GlossaryEntry(term="Prog", canonical="Proog", note="A character.", sources=[])],
    skipped=False,
)
OPTS = RunOptions(profile="entertainment", mode="extended")


def _user(call) -> str:
    user = next(m for m in call["messages"] if m["role"] == "user")
    return user["content"]


def _run(answer: dict, events=EVENTS, words=WORDS, gaps=GAPS, research=RESEARCH, opts=OPTS):
    fake = FakeTF([answer])
    return plan(events, words, gaps, research, opts, fake, run_id="r1"), fake


# -- word counting -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("A slide reads: 2.1 million", 5),
        ("Rain falls — hard.", 3),
        ("A well-known sign reads: OPEN 24/7.", 6),
        ("  ", 0),
        ("… — !", 0),
        ("", 0),
    ],
)
def test_count_words(text, n):
    assert count_words(text) == n


# -- pre-filter ------------------------------------------------------------------------


def test_said_in_dialogue_low_salience_event_never_sent():
    _, fake = _run({"cast": [], "items": [_item("e001")]})
    user = _user(fake.calls[0])
    assert "e002" not in user and "points at the slide" not in user
    assert "e003" in user and "gantry" in user  # salience 5 is still sent
    assert "e001" in user and "e004" in user


def test_no_events_left_means_no_model_call():
    only_said = [_ev("e001", 1, 2, said=True, sal=4), _ev("e002", 3, 4, said=True, sal=1)]
    fake = FakeTF([])
    for events in ([], only_said):
        out = plan(events, WORDS, GAPS, RESEARCH, OPTS, fake, run_id="r1")
        assert out == PlanResult(variants=[], cast={}, priority={})
    assert fake.calls == []


# -- request ---------------------------------------------------------------------------


def test_request_uses_ultra_with_thinking_and_tagged_sections():
    _, fake = _run({"cast": [], "items": []})
    call = fake.calls[0]  # the second asks again for the salient events left without an item
    assert call["model"] == ULTRA
    assert call["schema"] == PLAN_SCHEMA
    assert call["thinking"] is True
    assert call["max_tokens"] == 12000  # 3 events sent
    assert call["temperature"] == 0.6
    assert call["estimate"] == pytest.approx(0.02 + 0.0008 * 3)
    assert call["run_id"] == "r1" and call["stage"] == "plan" and call["role"] == "plan"
    system = next(m for m in call["messages"] if m["role"] == "system")
    assert system["content"] == load_prompt()
    # the system text carries the fix-round rules verbatim
    assert (
        "Rules 1, 5 and 11 apply to your own words, never to text you quote from the screen."
        in system["content"]
    )
    assert (
        "Room never decides whether an important event is included. Always include every "
        "priority 4–5 event even when no pause looks big enough; software may offer it as an "
        "extended description." in system["content"]
    )

    user = _user(call)
    for tag in ("transcript", "visual_events", "gaps", "glossary"):
        assert f"<{tag}>" in user and f"</{tag}>" in user
    assert user.index("<transcript>") < user.index("<visual_events>") < user.index("<gaps>")
    # transcript: absolute times, speaker-tagged lines
    assert "[61.0–62.0] S1: Hello there." in user
    assert "[63.0–63.9] S2: Proog, wait!" in user
    # events: id, times, kind, salience, text, verbatim (escaped, one line per event)
    line = next(ln for ln in user.splitlines() if ln.startswith("e001"))
    assert "2.0–6.0" in line and "slide" in line and "salience 5" in line
    assert "The slide is titled Water use." in line
    assert '"Water use by sector\\n2.1 million litres"' in line
    assert "said in dialogue" in next(ln for ln in user.splitlines() if ln.startswith("e003"))
    assert "said in dialogue" not in line
    # gaps: id, usable seconds, capacity words
    g1 = next(ln for ln in user.splitlines() if ln.startswith("g001"))
    assert "3.4 s" in g1 and "10 words" in g1
    # glossary and profile / mode
    assert "Proog" in user.split("<glossary>")[1]
    assert "entertainment" in user and "extended" in user


@pytest.mark.parametrize(
    ("n", "tokens", "estimate"),
    [(1, 12000, 0.0208), (10, 12000, 0.028), (53, 12000, 0.0624), (100, 19000, 0.10),
     (186, 31900, 0.1688), (300, 32000, 0.25)],
)  # fmt: skip
def test_budget_scales_with_events_sent(n, tokens, estimate):
    got_tokens, got_estimate = budget(n)
    assert got_tokens == tokens
    assert got_estimate == pytest.approx(estimate)


def test_request_budget_follows_events_sent():
    events = [_ev(f"e{i:03d}", i, i + 0.5, sal=4) for i in range(1, 101)]
    events.append(_ev("e101", 200, 201, sal=4, said=True))  # filtered out, not counted
    _, fake = _run({"cast": [], "items": [_item("e001")]}, events=events)
    call = fake.calls[0]
    assert call["max_tokens"] == 19000
    assert call["estimate"] == pytest.approx(0.10)


def test_request_placeholders_for_no_speech_no_gaps_no_glossary():
    empty = ResearchResult(glossary=[], skipped=True)
    _, fake = _run({"cast": [], "items": []}, words=[], gaps=[], research=empty)
    user = _user(fake.calls[0])
    for tag in ("transcript", "gaps", "glossary"):
        body = user.split(f"<{tag}>")[1].split(f"</{tag}>")[0].strip()
        assert body.startswith("(") and body.endswith(")")


# -- answer post-processing --------------------------------------------------------------

LONG_23 = " ".join(["word"] * 23)
LONG_22 = " ".join(["word"] * 22)


def test_words_counted_in_code_and_over_long_variant_dropped():
    answer = {
        "cast": [],
        "items": [
            _item("e001", 5, short="A slide reads: 2.1 million", medium="  ", long=LONG_23),
            _item("e004", 2, short="An office.", medium=None, long=LONG_22),
        ],
    }
    out, _ = _run(answer)
    got = [(v.event_id, v.length, v.words) for v in out.variants]
    assert got == [("e001", "short", 5), ("e004", "short", 2), ("e004", "long", 22)]
    assert out.variants[0].text == "A slide reads: 2.1 million"
    assert MAX_WORDS == 22


def test_unknown_and_filtered_event_ids_dropped():
    answer = {
        "cast": [],
        "items": [_item("e999"), _item("e002"), _item("e003", 5, short="A bald person waits.")],
    }
    out, _ = _run(answer)
    assert {v.event_id for v in out.variants} == {"e003"}
    assert out.priority == {"e003": 5}


def test_all_items_unknown_raises():
    with pytest.raises(ValueError, match="event id"):
        _run({"cast": [], "items": [_item("1"), _item("2")]})


def test_answer_without_items_raises():
    with pytest.raises(ValueError, match="items"):
        _run({"cast": []})


def test_cast_maps_name_to_descriptor():
    answer = {
        "cast": [
            {"descriptor": " a bald person in a dark coat ", "name": " Proog "},
            {"descriptor": "", "name": "Nobody"},
            {"descriptor": "a person in a red scarf", "name": "  "},
            {"descriptor": "a young person in a green shirt", "name": "Emo"},
        ],
        "items": [_item("e003")],
    }
    out, _ = _run(answer)
    assert out.cast == {
        "Proog": "a bald person in a dark coat",
        "Emo": "a young person in a green shirt",
    }


def test_cast_duplicate_name_keeps_first_descriptor_and_warns(caplog):
    answer = {
        "cast": [
            {"descriptor": "a bald person in a dark coat", "name": "Proog"},
            {"descriptor": "a bald person in a dark coat", "name": "Proog"},  # same: no warning
            {"descriptor": "a person on a gantry", "name": "Proog"},  # conflict
        ],
        "items": [_item("e003")],
    }
    with caplog.at_level(logging.WARNING, logger="lull.stages.plan"):
        out, _ = _run(answer)
    assert out.cast == {"Proog": "a bald person in a dark coat"}
    conflicts = [r for r in caplog.records if "Proog" in r.getMessage()]
    assert len(conflicts) == 1 and conflicts[0].levelno == logging.WARNING


def test_logs_events_sent_vs_items(caplog):
    answer = {"cast": [], "items": [_item("e001", 5), _item("e999")]}
    with caplog.at_level(logging.INFO, logger="lull.stages.plan"):
        _run(answer)
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("3 events sent" in m and "1 got an item" in m for m in info), info


@pytest.mark.parametrize(("given", "want"), [(9, 5), (0, 1), (-3, 1), (4, 4)])
def test_priority_clamped(given, want):
    out, _ = _run({"cast": [], "items": [_item("e001", given)]})
    assert out.priority == {"e001": want}


def test_event_with_no_surviving_variant_dropped_from_priority():
    answer = {
        "cast": [],
        "items": [
            _item("e001", 5, short="", medium=None, long=LONG_23),
            _item("e004", 2, short="A small office."),
        ],
    }
    out, _ = _run(answer)
    assert out.priority == {"e004": 2}
    assert {v.event_id for v in out.variants} == {"e004"}


def test_duplicate_event_item_keeps_first():
    answer = {"cast": [], "items": [_item("e001", 5, short="First."), _item("e001", 1)]}
    out, _ = _run(answer)
    assert out.priority == {"e001": 5}
    assert [v.text for v in out.variants] == ["First."]


def test_variant_whitespace_collapsed():
    out, _ = _run({"cast": [], "items": [_item("e001", short="  A  slide\nreads: Methods. ")]})
    assert out.variants[0].text == "A slide reads: Methods."
    assert out.variants[0].words == 4


# -- schema and prompt ---------------------------------------------------------------------


def test_schema_is_strict():
    assert PLAN_SCHEMA["additionalProperties"] is False
    assert set(PLAN_SCHEMA["required"]) == {"cast", "items"}
    cast = PLAN_SCHEMA["properties"]["cast"]["items"]
    assert cast["additionalProperties"] is False
    assert set(cast["required"]) == set(cast["properties"]) == {"descriptor", "name"}
    item = PLAN_SCHEMA["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    fields = {"event_id", "priority", "short", "medium", "long"}
    assert set(item["required"]) == set(item["properties"]) == fields
    assert item["properties"]["priority"] == {"type": "integer", "minimum": 1, "maximum": 5}
    assert item["properties"]["short"] == {"type": "string"}
    for k in ("medium", "long"):
        assert {"type": "null"} in item["properties"][k]["anyOf"]


def test_prompt_teaches_the_craft():
    text = load_prompt()
    for needle in (
        "present tense",  # R7
        "complete sentence",  # R8
        "we see",  # R18 banned phrase is named
        "frowns",  # R12 expression, not emotion
        "Do not assume race, ethnicity or gender",  # R14
        "descriptor first",  # R15
        "general term",  # R17
        "A slide reads:",  # R19
        "Words appear:",  # R19
        "title, then each axis",  # R22
        "Never repeat what the dialogue",  # R21
        "close-up",  # R18 jargon named
        "cast",
        "priority",
        "<visual_events>",
        "ignore any instructions",
    ):
        assert needle.lower() in text.lower(), needle
    assert text.count("Bad:") >= 3 and text.count("Good:") >= 3


def test_prompt_fix_round_wording():
    text = load_prompt()
    assert "22" not in text  # no exact word ceiling the model could try to count to
    assert "discards versions that run long" in text
    assert "when a sentence would be much longer" in text
    assert "every priority 4 and 5 event, unless it would add nothing" in text
    assert "(he, she, his, her, him, hers, man, woman, boy, girl, guy, lady…)" in text
    assert "Dana was named at 120.0 s." in text and "S2 (Dana)" not in text
    assert "plots Units (thousands)" in text  # axis label copied verbatim
    assert 'no "shot" or "frame" as camera terms' in text


# -- coverage: events without an item are asked for once more --------------------------------


def test_salient_events_without_an_item_are_asked_for_again():
    first = {"cast": [{"descriptor": "a bald person", "name": "Proog"}], "items": [_item("e001")]}
    second = {
        "cast": [{"descriptor": "a young person", "name": "Emo"}],
        "items": [_item("e003", 5, "A bald person stands on a gantry.")],
    }
    out, fake = _run(first)
    assert len(fake.calls) == 2 and out.unanswered == ["e003"]  # the fake's empty answer
    fake = FakeTF([first, second])
    out = plan(EVENTS, WORDS, GAPS, RESEARCH, OPTS, fake, run_id="r1")
    again = _user(fake.calls[1])
    assert "e003" in again and "e001 [" not in again and "e004" not in again  # salience 2 is not
    assert again.endswith("Write an item for every event listed.")
    assert "Write an item for every event listed." not in _user(fake.calls[0])
    assert (fake.calls[1]["max_tokens"], fake.calls[1]["estimate"]) == budget(1)
    assert (fake.calls[1]["model"], fake.calls[1]["role"]) == (ULTRA, "plan")
    assert out.priority == {"e001": 3, "e003": 5}
    assert [v.event_id for v in out.variants] == ["e001", "e003"]
    assert out.cast == {"Proog": "a bald person", "Emo": "a young person"}
    assert out.unanswered == []


def test_no_second_call_when_every_salient_event_has_an_item():
    out, fake = _run({"cast": [], "items": [_item("e001"), _item("e003")]})
    assert len(fake.calls) == 1 and out.unanswered == []  # e004 (salience 2) may stay out


def test_an_unusable_second_answer_keeps_the_first_and_flags_the_rest(caplog):
    first = {"cast": [], "items": [_item("e001")]}
    fake = FakeTF([first, {"cast": [], "items": [_item("e999")]}])
    with caplog.at_level(logging.WARNING, logger="lull.stages.plan"):
        out = plan(EVENTS, WORDS, GAPS, RESEARCH, OPTS, fake, run_id="r1")
    assert out.priority == {"e001": 3} and out.unanswered == ["e003"]
    assert "second answer" in caplog.text


def test_prompt_asks_for_an_item_per_event():
    text = load_prompt()
    assert "Write an item for every event you are sent." in text
    assert "Leave an event out entirely" not in text


# -- near-duplicate items (ruling, Task 16) ---------------------------------------------------


RUNS = [
    _ev("e010", 18.0, 22.0, sal=4, text="The smaller person runs along a platform."),
    _ev("e011", 29.0, 33.0, sal=4, text="The smaller person runs along a platform again."),
    _ev("e012", 40.0, 44.0, sal=4, text="The smaller person runs on."),
    _ev("e013", 41.0, 45.0, sal=4, text="The two people climb a slanted beam."),
]


def test_near_duplicate_items_are_dropped_keeping_the_higher_priority():
    """ED90: three "The smaller person runs..." lines within 30 s. The short variants'
    Jaccard is at least 0.6, so one is kept: the higher priority, the earlier on a tie."""
    answer = {"cast": [], "items": [
        _item("e010", 2, "The smaller person runs along a narrow platform.", "Medium one."),
        _item("e011", 3, "The smaller person runs along the narrow platform.", "Medium two."),
        _item("e012", 3, "Along a narrow platform the smaller person runs.", None, "Long."),
        _item("e013", 2, "The two people climb a slanted metal beam."),
    ]}  # fmt: skip
    result, _ = _run(answer, events=RUNS)
    assert result.priority == {"e011": 3, "e013": 2}
    assert result.duplicates == {"e010": "e011", "e012": "e011"}
    assert sorted({v.event_id for v in result.variants}) == ["e011", "e013"]
    assert [v.text for v in result.variants if v.event_id == "e011"] == [
        "The smaller person runs along the narrow platform.", "Medium two."
    ]  # fmt: skip


def test_near_duplicates_need_similar_short_lines_within_30_s():
    events = [
        _ev("e020", 0.0, 2.0, sal=4), _ev("e021", 31.0, 33.0, sal=4),  # 31 s apart
        _ev("e022", 40.0, 41.0, sal=4), _ev("e023", 42.0, 43.0, sal=4),  # unlike
        _ev("e024", 50.0, 51.0, sal=4), _ev("e025", 80.0, 81.0, sal=4),  # exactly 30 s
        _ev("e026", 120.0, 121.0, sal=4),
    ]  # fmt: skip
    answer = {"cast": [], "items": [
        _item("e020", 3, "A red door opens slowly."),
        _item("e021", 3, "A red door opens slowly."),
        _item("e022", 3, "The smaller person runs along a narrow platform."),
        _item("e023", 3, "The smaller person runs across a narrow beam."),  # Jaccard 0.5
        _item("e024", 4, "Snow falls on the empty square.", "Medium."),
        _item("e025", 4, "Snow falls on an empty square."),
        _item("e026", 5, "Snow falls on the empty square."),  # 70 s after e024
    ]}  # fmt: skip
    result, _ = _run(answer, events=events)
    assert result.duplicates == {"e025": "e024"}
    assert set(result.priority) == {"e020", "e021", "e022", "e023", "e024", "e026"}


def test_near_duplicates_never_chain_past_30_s():
    """M1: a dropped item always points at a kept twin within 30 s. Three alike lines 25 s
    apart with rising priority keep the first and the last; the middle one goes to the last."""
    events = [_ev("e030", 0.0, 1.0, sal=4), _ev("e031", 25.0, 26.0, sal=4),
              _ev("e032", 50.0, 51.0, sal=4)]  # fmt: skip
    answer = {"cast": [], "items": [
        _item("e030", 2, "Snow falls on the empty square."),
        _item("e031", 3, "Snow falls on the empty square."),
        _item("e032", 4, "Snow falls on the empty square."),
    ]}  # fmt: skip
    result, _ = _run(answer, events=events)
    assert result.duplicates == {"e031": "e032"}
    assert set(result.priority) == {"e030", "e032"}
    by_id = {e.id: e for e in events}
    for dropped, kept in result.duplicates.items():
        assert abs(by_id[dropped].t_start - by_id[kept].t_start) <= 30.0


def test_numbered_or_differently_quoted_lines_are_never_near_duplicates():
    """Step 1/2/3 slides, years, chapter numbers and different quoted text are different
    content even when the template-like short lines share most words."""
    slides = [
        _ev("e040", 0.0, 4.0, "slide", "A slide.", sal=5, verb="Step 1"),
        _ev("e041", 5.0, 9.0, "slide", "A slide.", sal=5, verb="Step 2"),
        _ev("e042", 10.0, 14.0, "slide", "A slide.", sal=5, verb="Step 3"),
        _ev("e043", 15.0, 16.0, "chart", "A chart.", sal=4),
        _ev("e044", 17.0, 18.0, "chart", "A chart.", sal=4),
        _ev("e045", 19.0, 20.0, "slide", "A title.", sal=4),
        _ev("e046", 21.0, 22.0, "slide", "A title.", sal=4),
        _ev("e047", 23.0, 24.0, "on_screen_text", "A sign.", sal=4),
        _ev("e048", 25.0, 26.0, "on_screen_text", "A sign.", sal=4),
        _ev("e049", 27.0, 28.0, "slide", "A slide.", sal=4, verb="Step 3"),  # repeats e042
    ]  # fmt: skip
    answer = {"cast": [], "items": [
        _item("e040", 4, 'A slide reads: "Step 1".'),
        _item("e041", 4, 'A slide reads: "Step 2".'),
        _item("e042", 4, 'A slide reads: "Step 3".'),
        _item("e043", 3, "Sales for 2019 rise sharply."),
        _item("e044", 3, "Sales for 2020 rise sharply."),
        _item("e045", 3, "The title Chapter One appears."),
        _item("e046", 3, "The title Chapter Two appears."),
        _item("e047", 3, 'A sign reads "Exit" above the door.'),
        _item("e048", 3, 'A sign reads "Enter" above the door.'),
        _item("e049", 2, 'A slide reads: "Step 3".'),
    ]}  # fmt: skip
    result, _ = _run(answer, events=slides)
    assert result.duplicates == {"e049": "e042"}  # the same step shown again: still a repeat


def test_a_failed_second_call_keeps_the_first_answer(caplog):
    """The first answer is paid for: a model error on the second call (asked for the events
    it left out) keeps it, and the rest are flagged, instead of failing the stage."""
    from lull.tf import TFError

    first = {"cast": [], "items": [_item("e001")]}

    class Flaky(FakeTF):
        def json(self, model, messages, schema, **kw):
            if self.calls:
                self.calls.append(kw)
                raise TFError("HTTP 503 after retries")
            return super().json(model, messages, schema, **kw)

    fake = Flaky([first])
    with caplog.at_level(logging.WARNING, logger="lull.stages.plan"):
        out = plan(EVENTS, WORDS, GAPS, RESEARCH, OPTS, fake, run_id="r1")
    assert len(fake.calls) == 2
    assert out.priority == {"e001": 3} and out.unanswered == ["e003"]
    assert "second answer" in caplog.text
