import json

import httpx
import pytest

from lull.ledger import TAVILY_CREDIT_USD, BudgetExceeded, Ledger
from lull.stages.research import GlossaryEntry, ResearchResult, research
from lull.tavily import Tavily, TavilyError
from lull.types import RunOptions, VisualEvent, Word


class FakeTF:
    def __init__(self, terms: list[str], kind: str = "person"):
        self.terms = terms
        self.kind = kind
        self.calls: list[dict] = []

    def json(self, model, messages, schema, **kw):
        self.calls.append({"model": model, "messages": messages, "kw": kw})
        return {"terms": [{"term": t, "kind": self.kind, "why": "x"} for t in self.terms]}


def _ev() -> list[VisualEvent]:
    return [
        VisualEvent(
            id="e001",
            t_start=0,
            t_end=2,
            kind="on_screen_text",
            text="a title card",
            verbatim_text="Elephants Dream",
            salience=4,
            said_in_dialogue=False,
        )
    ]


_SAID = "Proog and Emo met A1 B2 C3 D4 Bad X AI"
WORDS = [Word(text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in enumerate(_SAID.split())]


def _tavily(tmp_path, handler, sleep=lambda s: None):
    ledger = Ledger(tmp_path / "l.db", cap_run=None, cap_day=None, cap_total=None)
    return Tavily(
        "tvly-secret", ledger, transport=httpx.MockTransport(handler), sleep=sleep
    ), ledger


def _handler(seen: list):
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.url.path, body, req.headers.get("authorization")))
        if req.url.path == "/search":
            q = body["query"]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": f"About {q} film",
                            "url": f"https://x.test/{q[:5]}",
                            "content": f"{q} is a thing. " * 40,
                            "score": 0.9,
                        },
                        {
                            "title": "Other",
                            "url": "https://y.test/",
                            "content": "meh",
                            "score": 0.5,
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": body["urls"][0], "raw_content": "Source Page Title\n" + "z" * 3000}
                ],
                "failed_results": [],
            },
        )

    return h


def test_two_terms_two_searches_and_ledger(tmp_path):
    seen: list = []
    tv, ledger = _tavily(tmp_path, _handler(seen))
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["Proog", "Emo"]), tv, run_id="r")
    assert isinstance(res, ResearchResult) and not res.skipped
    assert [p for p, _, _ in seen] == ["/search", "/search"]
    assert seen[0][2] == "Bearer tvly-secret"
    assert [e.term for e in res.glossary] == ["Proog", "Emo"]
    e = res.glossary[0]
    assert (
        e.canonical == "Proog"
        and len(e.note) <= 200
        and e.sources == ["https://x.test/Proog", "https://y.test/"]
    )
    calls = ledger.calls("r")
    assert len(calls) == 2
    assert all(
        c.provider == "tavily"
        and c.model == "tavily-search"
        and c.stage == "research"
        and c.role == "research"
        and c.tokens_in == 0
        for c in calls
    )
    assert calls[0].cost_usd == pytest.approx(TAVILY_CREDIT_USD)


def test_max_terms_respected(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    tf = FakeTF(["A1", "B2", "C3", "D4"])
    res = research(_ev(), WORDS, RunOptions(), tf, tv, run_id="r", max_terms=2)
    assert len(res.glossary) == 2 and len(seen) == 2
    assert (
        "2" in tf.calls[0]["messages"][0]["content"] or "2" in tf.calls[0]["messages"][1]["content"]
    )


def test_source_url_extracts_first(tmp_path):
    seen: list = []
    tv, ledger = _tavily(tmp_path, _handler(seen))
    tf = FakeTF(["Proog"])
    res = research(_ev(), WORDS, RunOptions(source_url="https://src.test/p"), tf, tv, run_id="r")
    assert seen[0][0] == "/extract" and seen[0][1]["urls"] == ["https://src.test/p"]
    assert seen[1][0] == "/search"
    assert res.source_context and res.source_context.startswith("Source Page Title")
    assert len(res.source_context) <= 1500
    assert "Source Page Title" in json.dumps(tf.calls[0]["messages"])
    assert [c.model for c in ledger.calls("r")] == ["tavily-extract", "tavily-search"]


def test_none_path_skips(tmp_path):
    res = research(
        _ev(),
        WORDS,
        RunOptions(source_url="https://src.test/p"),
        FakeTF(["Proog"]),
        None,
        run_id="r",
    )
    assert res.skipped and res.source_context is None
    assert res.glossary == [
        GlossaryEntry(term="Proog", canonical="Proog", note="", sources=[], kind="person")
    ]


def test_retry_on_429(tmp_path):
    n = {"c": 0}
    sleeps: list[float] = []

    def h(req):
        n["c"] += 1
        if n["c"] < 3:
            return httpx.Response(429, text="slow")
        return httpx.Response(200, json={"results": [], "usage": {"credits": 2}})

    tv, ledger = _tavily(tmp_path, h, sleep=sleeps.append)
    assert tv.search("q", run_id="r") == []
    assert n["c"] == 3 and sleeps == [1, 2]
    assert ledger.calls("r")[0].cost_usd == pytest.approx(2 * TAVILY_CREDIT_USD)


def test_error_raises_and_hides_key(tmp_path):
    tv, _ = _tavily(tmp_path, lambda r: httpx.Response(401, text="bad"))
    with pytest.raises(TavilyError) as ei:
        tv.search("q", run_id="r")
    assert "tvly-secret" not in str(ei.value)


def test_extract_credits_per_five(tmp_path):
    def h(req):
        return httpx.Response(200, json={"results": [], "failed_results": []})

    tv, ledger = _tavily(tmp_path, h)
    tv.extract([f"https://a.test/{i}" for i in range(6)], run_id="r")
    assert ledger.calls("r")[0].cost_usd == pytest.approx(2 * TAVILY_CREDIT_USD)


def test_canonical_casing_from_results(tmp_path):
    def h(req):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Proog - Elephants Dream wiki",
                        "url": "https://x.test/",
                        "content": "...",
                    }
                ]
            },
        )

    tv, _ = _tavily(tmp_path, h)
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["proog"]), tv, run_id="r")
    assert res.glossary[0].canonical == "Proog"


@pytest.mark.parametrize(
    ("term", "title", "content"),
    [("Emo", "Emotion in film", "emotional"), ("AI", "Fresh rain", "it was raining")],
)
def test_canonical_rejects_substring(tmp_path, term, title, content):
    def h(req):
        return httpx.Response(
            200, json={"results": [{"title": title, "url": "https://x.test/", "content": content}]}
        )

    tv, _ = _tavily(tmp_path, h)
    res = research(_ev(), WORDS, RunOptions(), FakeTF([term]), tv, run_id="r")
    assert res.glossary[0].canonical == term


def test_canonical_falls_through_to_later_result(tmp_path):
    def h(req):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Emotion", "url": "https://a.test/", "content": "none"},
                    {"title": "Meet EMO the robot", "url": "https://b.test/", "content": "x"},
                ]
            },
        )

    tv, _ = _tavily(tmp_path, h)
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["Emo"]), tv, run_id="r")
    assert res.glossary[0].canonical == "EMO"


def test_empty_input_no_calls(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    tf = FakeTF(["X"])
    res = research([], [], RunOptions(), tf, tv, run_id="r")
    assert res.glossary == [] and res.skipped is False and not tf.calls and not seen
    res = research([], [], RunOptions(), tf, None, run_id="r")
    assert res.glossary == [] and res.skipped is True and not tf.calls


def test_untrusted_tags_and_term_only_query(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    tf = FakeTF(["Proog"])
    research(_ev(), WORDS, RunOptions(source_url="https://src.test/p"), tf, tv, run_id="r")
    msgs = tf.calls[0]["messages"]
    user = msgs[1]["content"]
    for tag in ("transcript", "on_screen_text", "visual_events", "source_page"):
        assert f"<{tag}>" in user and f"</{tag}>" in user
    assert "ignore" in msgs[0]["content"].lower() and "<transcript>" in msgs[0]["content"]
    assert seen[1][1]["query"] == "Proog"


def test_extract_failure_degrades(tmp_path):
    def h(req):
        if req.url.path == "/extract":
            return httpx.Response(400, text="nope")
        return _handler([])(req)

    tv, _ = _tavily(tmp_path, h)
    res = research(
        _ev(), WORDS, RunOptions(source_url="https://s.test/"), FakeTF(["Proog"]), tv, run_id="r"
    )
    assert res.source_context is None and len(res.errors) == 1 and "extract" in res.errors[0]
    assert res.glossary[0].sources


def test_search_failure_degrades(tmp_path):
    def h(req):
        if json.loads(req.content)["query"] == "Bad":
            return httpx.Response(400, text="nope")
        return _handler([])(req)

    tv, _ = _tavily(tmp_path, h)
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["Bad", "Proog"]), tv, run_id="r")
    assert res.glossary[0] == GlossaryEntry(
        term="Bad", canonical="Bad", note="lookup failed", sources=[], kind="person"
    )
    assert res.glossary[1].sources and len(res.errors) == 1


def test_budget_exceeded_propagates(tmp_path):
    class Broke(FakeTF):
        def json(self, *a, **kw):
            raise BudgetExceeded("run")

    tv, _ = _tavily(tmp_path, _handler([]))
    with pytest.raises(BudgetExceeded):
        research(_ev(), WORDS, RunOptions(), Broke(["Proog"]), tv, run_id="r")


def test_research_stops_at_the_search_cap_and_says_so(tmp_path):
    """Searches are capped per run (LULL_TAVILY_RUN): research looks up what it may, keeps
    the other terms as heard, and records that it was capped; that is not an error."""
    seen: list = []
    ledger = Ledger(tmp_path / "c.db", cap_run=None, cap_day=None, cap_total=None, tavily_run=2)
    tv = Tavily("k", ledger, transport=httpx.MockTransport(_handler(seen)))
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["Proog", "Emo", "Bad"]), tv, run_id="r")
    assert [p for p, _, _ in seen] == ["/search", "/search"]
    assert [g.note for g in res.glossary][2] == "lookup skipped: search cap"
    assert res.glossary[0].sources and res.glossary[1].sources
    assert res.notes == ["research capped: 2 searches"] and res.errors == []


def test_descriptors_dropped_no_searches(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    res = research(
        _ev(), WORDS, RunOptions(), FakeTF(["metal walkway", "bald man"]), tv, run_id="r"
    )
    assert res.glossary == [] and seen == []


def test_event_text_is_not_evidence(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["title card"]), tv, run_id="r")
    assert res.glossary == [] and seen == []


def test_spoken_name_kept(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    words = [Word(text="Emo", start=0, end=1)]
    res = research([], words, RunOptions(), FakeTF(["Emo"]), tv, run_id="r")
    assert [e.term for e in res.glossary] == ["Emo"] and len(seen) == 1


def test_verbatim_only_kept(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    res = research(_ev(), [], RunOptions(), FakeTF(["Elephants Dream"], "work"), tv, run_id="r")
    assert [e.term for e in res.glossary] == ["Elephants Dream"] and len(seen) == 1


def test_kind_other_dropped(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    res = research(_ev(), WORDS, RunOptions(), FakeTF(["Proog"], "other"), tv, run_id="r")
    assert res.glossary == [] and seen == []


def test_filter_before_max_terms(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    tf = FakeTF(["junk one", "junk two", "junk three", "Proog", "Emo"])
    res = research(_ev(), WORDS, RunOptions(), tf, tv, run_id="r", max_terms=2)
    assert [e.term for e in res.glossary] == ["Proog", "Emo"] and len(seen) == 2


class KindsTF:
    """Lightning answering with a kind per term."""

    def __init__(self, pairs: list[tuple[str, str]]):
        self.pairs = pairs

    def json(self, model, messages, schema, **kw):
        return {"terms": [{"term": t, "kind": k, "why": "x"} for t, k in self.pairs]}


_LECTURE = "We study the seafloor at the USGS with volcanologists".split()
LECTURE = [Word(text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in enumerate(_LECTURE)]
SLIDE = [_ev()[0].model_copy(update={"verbatim_text": "Photosynthesis in kelp forests"})]


def test_technical_terms_are_looked_up_only_when_on_screen(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    tf = KindsTF([("seafloor", "technical"), ("volcanologists", "technical"),
                  ("photosynthesis", "technical"), ("USGS", "organization")])  # fmt: skip
    res = research(SLIDE, LECTURE, RunOptions(), tf, tv, run_id="r")
    assert [(e.term, e.kind) for e in res.glossary] == [
        ("photosynthesis", "technical"), ("USGS", "organization"),
    ]  # fmt: skip
    assert [body["query"] for _, body, _ in seen] == ["photosynthesis", "USGS"]


def test_glossary_entries_carry_their_kind_also_without_tavily():
    tf = KindsTF([("Proog", "person"), ("Elephants Dream", "work"), ("Proog", "place")])
    res = research(_ev(), WORDS, RunOptions(), tf, None, run_id="r")
    assert [(e.term, e.kind) for e in res.glossary] == [
        ("Proog", "person"), ("Elephants Dream", "work"),
    ]  # fmt: skip
    assert GlossaryEntry(term="x", canonical="x", note="").kind == "other"  # older caches


def test_at_most_five_terms_by_default(tmp_path):
    seen: list = []
    tv, _ = _tavily(tmp_path, _handler(seen))
    names = ["Proog", "Emo", "A1", "B2", "C3", "D4", "Bad"]  # all said in WORDS
    res = research(_ev(), WORDS, RunOptions(), FakeTF(names), tv, run_id="r")
    assert [e.term for e in res.glossary] == names[:5] and len(seen) == 5
