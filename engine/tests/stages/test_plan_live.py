import os

import pytest

from lull.ledger import Ledger
from lull.stages.plan import MAX_WORDS, plan
from lull.stages.research import GlossaryEntry, ResearchResult
from lull.tf import TokenFactory
from lull.types import Gap, RunOptions, VisualEvent, Word

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LULL_LIVE") != "1", reason="set LULL_LIVE=1 to run"),
]

EVENTS = [
    VisualEvent(
        id="e001", t_start=0.5, t_end=4.0, kind="on_screen_text",
        text="A title appears over a dark sky.", verbatim_text="Elephants Dream",
        salience=5, said_in_dialogue=False,
    ),
    VisualEvent(
        id="e002", t_start=4.0, t_end=9.0, kind="person",
        text="A bald person in a dark coat stands on a narrow metal bridge.",
        verbatim_text=None, salience=4, said_in_dialogue=False,
    ),
    VisualEvent(
        id="e003", t_start=9.5, t_end=12.0, kind="action",
        text="The bald person grabs a younger person by the arm and pulls them back.",
        verbatim_text=None, salience=5, said_in_dialogue=False,
    ),
]  # fmt: skip
WORDS = [
    Word(text="Proog,", start=10.0, end=10.4, speaker=1),
    Word(text="wait!", start=10.5, end=10.9, speaker=1),
    Word(text="Emo,", start=11.2, end=11.6, speaker=2),
    Word(text="don't", start=11.7, end=12.0, speaker=2),
    Word(text="touch", start=12.1, end=12.4, speaker=2),
    Word(text="that.", start=12.5, end=12.9, speaker=2),
]
GAPS = [
    Gap(id="g001", start=0.0, end=10.0, usable_start=0.2, usable_end=9.8, capacity_words=28),
    Gap(id="g002", start=12.9, end=16.0, usable_start=13.1, usable_end=15.8, capacity_words=8),
]
RESEARCH = ResearchResult(
    glossary=[
        GlossaryEntry(term="Proog", canonical="Proog", note="Character in Elephants Dream."),
        GlossaryEntry(term="Emo", canonical="Emo", note="Character in Elephants Dream."),
    ],
    skipped=False,
)


def test_plan_live(tmp_path):
    ledger = Ledger(tmp_path / "live.db")
    client = TokenFactory.from_settings(ledger)
    try:
        out = plan(
            EVENTS, WORDS, GAPS, RESEARCH, RunOptions(profile="entertainment"), client,
            run_id="live",
        )  # fmt: skip
    finally:
        client.close()
    assert len(out.variants) >= 1
    assert all(1 <= v.words <= MAX_WORDS for v in out.variants)
    assert out.priority and all(1 <= p <= 5 for p in out.priority.values())
    assert {v.event_id for v in out.variants} == set(out.priority)
    calls = ledger.calls("live")
    assert calls and all(c.stage == "plan" and c.model.endswith("Ultra-550b-a55b") for c in calls)
