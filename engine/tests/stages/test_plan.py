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
        return self.responses.pop(0)


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
    (call,) = fake.calls
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
    (call,) = fake.calls
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
