import json
from pathlib import Path

import httpx
import pytest

from lull import tf
from lull.config import Settings
from lull.ledger import BudgetExceeded, Ledger
from lull.tf import LIGHTNING, ULTRA, TFError, TokenFactory

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


def ok(content='{"a": 1}', finish="stop", usage=None):
    usage = usage or {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 200},
    }
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": usage,
        },
    )


class Server:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        return self.responses.pop(0)


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "l.db", cap_run=1.0, cap_day=10.0, cap_total=10.0)


def make(ledger, *responses):
    srv = Server(*responses)
    sleeps: list[float] = []
    client = TokenFactory("k", ledger, transport=httpx.MockTransport(srv), sleep=sleeps.append)
    return client, srv, sleeps


def call(client, model=LIGHTNING, **kw):
    return client.json(
        model, [{"role": "user", "content": "hi"}], SCHEMA,
        run_id="r1", stage="s", role="describe", **kw,
    )  # fmt: skip


def test_json_request_shape_and_record(ledger):
    client, srv, _ = make(ledger, ok())
    assert call(client) == {"a": 1}
    b = srv.bodies[0]
    assert b["model"] == LIGHTNING
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert b["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "s", "schema": SCHEMA, "strict": True},
    }
    assert b["max_tokens"] == 4000 and b["temperature"] == 0.6
    assert srv.headers[0]["authorization"] == "Bearer k"
    (rec,) = ledger.calls("r1")
    assert (rec.tokens_in, rec.tokens_cached, rec.tokens_out) == (1000, 200, 100)
    assert rec.provider == "tokenfactory" and rec.model == LIGHTNING and rec.cost_usd > 0
    assert rec.stage == "s" and rec.role == "describe"


def test_thinking_flag_passthrough(ledger):
    client, srv, _ = make(ledger, ok())
    call(client, thinking=True)
    assert srv.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_text_returns_string_without_response_format(ledger):
    client, srv, _ = make(ledger, ok("hello"))
    out = client.text(LIGHTNING, [], run_id="r1", stage="s", role="x")
    assert out == "hello"
    assert "response_format" not in srv.bodies[0]


def test_cache_hit_fallback_and_null_details(ledger):
    usage = {"prompt_tokens": 500, "completion_tokens": 5, "prompt_tokens_details": None,
             "prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 200}  # fmt: skip
    client, _, _ = make(ledger, ok(usage=usage))
    call(client)
    assert ledger.calls("r1")[0].tokens_cached == 300
    client, _, _ = make(ledger, ok(usage={"prompt_tokens": 5, "completion_tokens": 1}))
    call(client)
    assert ledger.calls("r1")[1].tokens_cached == 0


def test_fenced_json_parsed(ledger):
    client, _, _ = make(ledger, ok('```json\n{"a": 2}\n```'))
    assert call(client) == {"a": 2}


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_retried_with_backoff(ledger, status):
    client, srv, sleeps = make(
        ledger, httpx.Response(status), httpx.Response(status), httpx.Response(status), ok()
    )
    assert call(client) == {"a": 1}
    assert sleeps == [1, 2, 4]
    assert len(srv.bodies) == 4
    assert len(ledger.calls("r1")) == 1


def test_transient_exhausted_raises(ledger):
    client, srv, sleeps = make(ledger, *[httpx.Response(503, text="down")] * 4)
    with pytest.raises(TFError) as e:
        call(client)
    assert e.value.status == 503 and "down" in e.value.body
    assert sleeps == [1, 2, 4]


def test_network_error_retried(ledger):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return ok()

    sleeps: list[float] = []
    client = TokenFactory("k", ledger, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert call(client) == {"a": 1}
    assert sleeps == [1]


def test_402_is_provider_budget(ledger):
    client, _, sleeps = make(ledger, httpx.Response(402, text="no funds"))
    with pytest.raises(BudgetExceeded) as e:
        call(client)
    assert e.value.scope == "provider" and sleeps == []


def test_other_4xx_tferror_with_excerpt(ledger):
    client, srv, sleeps = make(ledger, httpx.Response(400, text="x" * 900))
    with pytest.raises(TFError) as e:
        call(client)
    assert e.value.status == 400 and len(e.value.body) == 500
    assert sleeps == [] and len(srv.bodies) == 1


def test_length_retries_with_doubled_max_tokens(ledger):
    client, srv, _ = make(ledger, ok('{"a":', finish="length"), ok())
    assert call(client, max_tokens=1000) == {"a": 1}
    assert [b["max_tokens"] for b in srv.bodies] == [1000, 2000]
    assert len(ledger.calls("r1")) == 2  # truncated attempt was billed


def test_parse_failure_then_truncated_error(ledger):
    client, srv, _ = make(ledger, ok("not json"), ok("still not"))
    with pytest.raises(TFError, match="truncated"):
        call(client, max_tokens=500)
    assert [b["max_tokens"] for b in srv.bodies] == [500, 1000]


def test_empty_content_is_truncation(ledger):
    client, srv, _ = make(ledger, ok(""), ok(""))
    with pytest.raises(TFError, match="truncated"):
        call(client)
    assert len(srv.bodies) == 2


def test_text_length_twice_raises(ledger):
    client, _, _ = make(ledger, ok("part", finish="length"), ok("part2", finish="length"))
    with pytest.raises(TFError, match="truncated"):
        client.text(LIGHTNING, [], run_id="r1", stage="s", role="x")


def test_budget_checked_before_every_attempt(tmp_path):
    tight = Ledger(tmp_path / "t.db", cap_run=0.001, cap_day=10, cap_total=10)
    client, srv, _ = make(tight, ok())
    with pytest.raises(BudgetExceeded) as e:
        call(client, estimate=0.01)
    assert e.value.scope == "run" and srv.bodies == []


def test_media_parts(tmp_path: Path):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"vid")
    p = tmp_path / "a.png"
    p.write_bytes(b"img")
    assert tf.video_part(v) == {
        "type": "video_url", "video_url": {"url": "data:video/mp4;base64,dmlk"}
    }  # fmt: skip
    assert tf.image_part(p) == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}
    }  # fmt: skip
    j = tmp_path / "a.JPG"
    j.write_bytes(b"x")
    assert tf.image_part(j)["image_url"]["url"].startswith("data:image/jpeg;base64,")
    w = tmp_path / "a.webm"
    w.write_bytes(b"x")
    assert tf.video_part(w)["video_url"]["url"].startswith("data:video/webm;base64,")


def test_video_part_too_big(tmp_path: Path):
    big = tmp_path / "big.mp4"
    big.write_bytes(b"0" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        tf.video_part(big)


def test_from_settings_missing_key(ledger, monkeypatch):
    monkeypatch.setattr(tf, "get_settings", lambda: Settings(_env_file=None, NEBIUS_API_KEY=None))
    with pytest.raises(TFError, match="NEBIUS_API_KEY"):
        TokenFactory.from_settings(ledger)


def test_from_settings_ok(ledger, monkeypatch):
    monkeypatch.setattr(tf, "get_settings", lambda: Settings(_env_file=None, NEBIUS_API_KEY="zz"))
    assert TokenFactory.from_settings(ledger).api_key == "zz"


def test_model_constants():
    assert ULTRA == "nvidia/Nemotron-3-Ultra-550b-a55b"


# -- edge cases -------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("cut")])
def test_a_request_that_may_have_reached_the_server_is_not_sent_again(ledger, error):
    """Only a failure to connect is retried: a read timeout or a dropped answer may be a call
    the provider ran (and billed), so sending it again could pay twice."""
    sent = {"n": 0}

    def handler(req):
        sent["n"] += 1
        raise error

    sleeps: list[float] = []
    client = TokenFactory("k", ledger, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    with pytest.raises(TFError, match="network error"):
        call(client)
    assert sent["n"] == 1 and sleeps == []


def test_a_connect_timeout_is_retried(ledger):
    sent = {"n": 0}

    def handler(req):
        sent["n"] += 1
        if sent["n"] < 3:
            raise httpx.ConnectTimeout("no route")
        return ok()

    sleeps: list[float] = []
    client = TokenFactory("k", ledger, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert call(client) == {"a": 1} and sleeps == [1, 2]


def test_leading_reasoning_is_dropped_before_parsing(ledger):
    client, _, _ = make(ledger, ok('<think>\nlet me see {"a": 0}\n</think>\n\n{"a": 3}'))
    assert call(client) == {"a": 3}
    client, _, _ = make(ledger, ok('<think>left open {"a": 0}'), ok('{"a": 4}'))
    assert call(client) == {"a": 4}  # an unclosed reasoning block is not an answer


def test_json_that_is_not_an_object_is_unparseable(ledger):
    client, srv, _ = make(ledger, ok("[1, 2]"), ok('"a"'))
    with pytest.raises(TFError, match="truncated"):
        call(client)
    assert len(srv.bodies) == 2


def test_the_cap_is_checked_again_before_a_retry(tmp_path):
    led = Ledger(tmp_path / "t.db", cap_run=0.0015, cap_day=10, cap_total=10)
    usage = {"prompt_tokens": 10_000, "completion_tokens": 1_000}  # Lightning: $0.00084
    client, srv, _ = make(led, ok('{"a":', finish="length", usage=usage), ok())
    with pytest.raises(BudgetExceeded) as e:
        call(client, estimate=0.001)  # 0.00084 spent + 0.001 > 0.0015
    assert e.value.scope == "run" and len(srv.bodies) == 1


def test_client_closes_as_a_context_manager(ledger):
    with TokenFactory("k", ledger, transport=httpx.MockTransport(Server(ok()))) as client:
        assert call(client) == {"a": 1}
    assert client._http.is_closed


def test_an_answer_without_usage_is_charged_at_its_estimate(ledger, caplog):
    """A 200 answer that reports no token usage still costs something: it is recorded at the
    call's estimate so the caps see it."""
    resp = httpx.Response(
        200, json={"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}]}
    )
    client, _, _ = make(ledger, resp)
    with caplog.at_level("WARNING", logger="lull.tf"):
        assert call(client, estimate=0.0123) == {"a": 1}
    [rec] = ledger.calls("r1")
    assert rec.cost_usd == pytest.approx(0.0123) and "usage" in caplog.text
