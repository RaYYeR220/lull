import wave
from pathlib import Path

import pytest

from lull.media import probe
from lull.stages import finalize as fin
from lull.stages.finalize import (
    CueContext,
    OverlapError,
    _lost_ahead,
    assert_no_overlap,
    compress,
    count_ahead,
    described_events,
    finalize_cue,
    introduce_names,
    names_in,
    overlap_report,
    refresh_described,
    split_sentences,
)
from lull.stages.verify import (
    TEXT_SCHEMA,
    VERIFY_SCHEMA,
    clip_window,
    verify,
)
from lull.stages.voice import atempo, voice
from lull.tf import LIGHTNING, VL
from lull.types import Claim, Cue, Gap, RunOptions, Span, VisualEvent

# -- fakes ---------------------------------------------------------------------------


def _wav(path: Path, seconds: float, rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * round(seconds * rate))
    return path


class FakeSpeech:
    """Writes a silent WAV per call: durations from a list, or from a function of the text."""

    def __init__(self, durations):
        self.durations = durations
        self.calls: list[dict] = []

    def synthesize(self, text, voice, dst):
        self.calls.append({"text": text, "voice": voice, "dst": Path(dst)})
        d = self.durations(text) if callable(self.durations) else self.durations.pop(0)
        return _wav(Path(dst), d)


def _range_of(claim: str, line: str) -> list[list[int]]:
    """The token ranges of `line` whose words a claim's text spells (case aside): the words
    together if they stand together, else each where it first comes in order; the whole line
    when they are not all in it."""
    from lull.stages.verify import fold, tokenize

    toks = [fold(t.text) for t in tokenize(line)]
    want = [fold(t.text) for t in tokenize(claim)]
    for i in range(len(toks) - len(want) + 1):
        if want and toks[i : i + len(want)] == want:
            return [[i, i + len(want) - 1]]
    found, at = [], 0
    for w in want:
        k = next((j for j in range(at, len(toks)) if toks[j] == w), None)
        if k is None:
            return [[0, max(0, len(toks) - 1)]]
        found.append(k)
        at = k + 1
    ranges: list[list[int]] = []
    for k in found:
        if ranges and ranges[-1][1] == k - 1:
            ranges[-1][1] = k
        else:
            ranges.append([k, k])
    return ranges or [[0, max(0, len(toks) - 1)]]


class FakeTF:
    """Answers by role: a list of canned answers, or a function of the call.

    A "verify" answer may be written as the claims a check finds, {"claims": [{text, verdict,
    at}]}: unless the test gives "split" answers of its own, the split call is answered with
    those claims (each on the tokens of the line it spells, else the whole line) and the
    judge call with their verdicts, by position. A mend call with no answer given is answered
    with the cut line as it is. `roles()` leaves such split calls out; `roles(splits=True)`
    keeps them."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls: list[dict] = []
        self._found: dict | None = None  # the claims a split call took from a verify answer

    def _answer(self, role, call):
        a = self.answers[role]
        return a(call) if callable(a) else a.pop(0)

    def json(self, model, messages, schema, **kw) -> dict:
        call = {"model": model, "messages": messages, "schema": schema, **kw}
        self.calls.append(call)
        role = kw["role"]
        if role == "split" and "split" not in self.answers:
            self._found = self._answer("verify", call)
            line = _description(call)
            return {"claims": [{"id": f"k{i}", "text": c.get("text") or "a claim",
                                "tokens": _range_of(c.get("text") or "", line)}
                               for i, c in enumerate(self._found.get("claims", []), start=1)]
                    or [{"id": "k1", "text": line, "tokens": _range_of("", line)}]}  # fmt: skip
        if role == "mend" and "mend" not in self.answers:
            return {"text": _description(call)}  # the cut line as it is
        if role == "verify" and "split" not in self.answers:
            found, self._found = self._found, None
            if found is None:  # the same line judged again: its split was kept
                found = self._answer("verify", call)
            if "claims" in found:
                return {"verdicts": [
                    {"id": f"k{i}", "verdict": c.get("verdict"), "evidence": "",
                     "at": c.get("at")}
                    for i, c in enumerate(found["claims"], start=1)
                ]}  # fmt: skip
            return found
        out = self._answer(role, call)
        if role == "split" and isinstance(out, dict):  # words given for tokens: their range
            line = _description(call)
            out = {"claims": [
                {**c, "tokens": _range_of(c["tokens"], line)} if isinstance(c.get("tokens"), str)
                else c for c in out.get("claims", [])
            ]}  # fmt: skip
        return out

    def roles(self, *, splits: bool = False) -> list[str]:
        return [c["role"] for c in self.calls if splits or c["role"] != "split"]

    def of(self, role: str) -> list[dict]:
        return [c for c in self.calls if c["role"] == role]


@pytest.fixture(autouse=True)
def _lenient_split(request, monkeypatch):
    """The FakeTF below answers a split with the claims a test's verify answer lists, each
    spanning the whole line, which rarely covers every word of it; outside the tests marked
    strict_split the split check only asks for unique ids and spans in the line."""
    if request.node.get_closest_marker("strict_split"):
        return
    from lull.stages import verify as ver

    def lenient(text, claims, names=()):
        n = len(ver.tokenize(text))
        problems = [f"claim {c.id} has tokens outside 0..{n - 1}" for c in claims
                    if any(not (0 <= a <= b < n) for a, b in c.tokens)]  # fmt: skip
        return problems + (["two claims share an id"]
                           if len({c.id for c in claims}) < len(claims) else [])  # fmt: skip

    monkeypatch.setattr(ver, "check_split", lenient)


def _user_text(call) -> str:
    content = next(m for m in call["messages"] if m["role"] == "user")["content"]
    if isinstance(content, str):
        return content
    return "\n".join(p["text"] for p in content if p.get("type") == "text")


def _description(call) -> str:
    text = _user_text(call)
    return text.split("<description>\n", 1)[1].split("\n</description>", 1)[0]


def _claims(*pairs, at=1.0):
    return {"claims": [{"text": t, "verdict": v, "at": at} for t, v in pairs]}


def _supported(*texts):
    return _claims(*((t, "supported") for t in texts))


# -- fixtures ------------------------------------------------------------------------

EV = VisualEvent(
    id="e001", t_start=2.0, t_end=6.0, kind="action", text="A person opens a door.",
    verbatim_text=None, salience=4, said_in_dialogue=False,
)  # fmt: skip
EVENTS = {"e001": EV}
GAP = Gap(id="g001", start=1.8, end=6.2, usable_start=2.0, usable_end=6.0, capacity_words=9)
TEXT = "A person in a red coat opens a door."
CUE = Cue(
    id="c001", gap_id="g001", start=2.0, end=None, text=TEXT, event_ids=["e001"],
    status="verified", ahead=False, claims=[], audio_path=None, audio_dur=None,
)  # fmt: skip
CTX = CueContext(wps=2.36, cast={}, named=set(), introduced=set(), verbatim=[],
                 proper_nouns=set(), video_duration=12.0)  # fmt: skip
OPTS = RunOptions(voice="Aria")
PROOG = "a bald person in a dark coat"


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


@pytest.fixture
def video(tmp_path, monkeypatch) -> Path:
    """A 12 s stand-in video: probe, clip cutting and audio measurement are done in Python
    (each ffmpeg launch costs about a second here). Tests that need real media use the
    session `tone_slides` fixture instead."""
    from lull.stages import voice as voice_mod

    def fake_cut(src, dst, start, end, *, height=480):
        Path(dst).write_bytes(b"\0" * 16)
        return Path(dst)

    def no_probe(path):  # the duration comes from CueContext.video_duration (probed once)
        raise AssertionError(f"probed {path}")

    monkeypatch.setattr(fin, "probe", no_probe)
    monkeypatch.setattr(fin, "cut_clip", fake_cut)
    monkeypatch.setattr(fin, "audio_duration", _wav_seconds)
    monkeypatch.setattr(voice_mod, "audio_duration", _wav_seconds)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"\0" * 16)
    return path


def _run(tmp_path, video, tf, speech, *, cue=CUE, gap=GAP, events=EVENTS, ctx=CTX):
    work = tmp_path / "work"
    return finalize_cue(cue, gap, events, video, work, tf, speech, OPTS, ctx, run_id="r1")


def _cue(text=TEXT, **kw) -> Cue:
    return CUE.model_copy(update={"text": text, **kw})


# -- (a) the happy path ----------------------------------------------------------------


def test_all_supported_and_short_audio_is_verified(tmp_path, tone_slides):
    tf = FakeTF(verify=[_supported("a person", "the person wears a red coat", "opens a door")])
    speech = FakeSpeech([3.0])
    ctx = CTX.model_copy(update={"video_duration": None})  # probed here instead
    out = _run(tmp_path, tone_slides, tf, speech, ctx=ctx)
    assert out.status == "verified"
    assert out.start == GAP.usable_start
    assert out.audio_dur == pytest.approx(3.0, abs=1e-3)
    assert out.end == pytest.approx(out.start + out.audio_dur)
    assert out.tempo == 1.0
    assert out.text == TEXT
    assert [c.verdict for c in out.claims] == ["supported"] * 3
    assert Path(out.audio_path) == tmp_path / "work" / "audio" / "c001.wav"
    assert speech.calls[0]["voice"] == "Aria" and speech.calls[0]["text"] == TEXT
    assert tf.roles() == ["verify"]
    call = tf.of("verify")[0]
    assert call["model"] == VL and call["schema"] == VERIFY_SCHEMA
    assert (call["stage"], call["thinking"], call["temperature"]) == ("finalize", False, 0.2)
    assert (call["max_tokens"], call["estimate"]) == (1500, 0.005)
    user = next(m for m in call["messages"] if m["role"] == "user")["content"]
    assert user[0]["type"] == "video_url"
    assert _description(call) == TEXT
    # the clip is cut around the cue's events: [2 - 1, 6 + 1]
    clip = tmp_path / "work" / "verify" / "c001.mp4"
    assert probe(clip).duration == pytest.approx(6.0, abs=0.15)
    assert probe(clip).height == 360


def test_claim_times_are_absolute(tmp_path, video):
    tf = FakeTF(verify=[_claims(("a person opens a door", "supported"), at=1.5)])
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert [(c.text, c.verdict, c.at) for c in out.claims] == [
        ("a person opens a door", "supported", 2.5)
    ]  # fmt: skip


# -- (b) an unsupported claim is cut, then the rest is re-verified -----------------------


def test_not_visible_claim_is_removed_and_reverified(tmp_path, video):
    """The engine cuts the failed claim's tokens itself; Lightning only mends the grammar of
    what is left, and the shorter line is split and judged again."""
    tf = FakeTF(
        verify=[
            _claims(
                ("a person", "supported"),
                ("in a red coat", "not_visible"),
                ("opens a door", "supported"),
            ),  # fmt: skip
            _supported("a person", "opens a door"),
        ],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "mend", "verify"]
    assert out.status == "verified"
    assert out.text == "A person opens a door."
    assert "1 claim removed: in a red coat" in out.note
    md = tf.of("mend")[0]
    assert (md["model"], md["stage"], md["thinking"]) == (LIGHTNING, "finalize", False)
    assert md["schema"] == TEXT_SCHEMA
    assert _description(md) == "A person opens a door."  # the engine's cut
    assert "<cut>\na, coat, in, red\n</cut>" in _user_text(md)
    assert _description(tf.of("verify")[1]) == "A person opens a door."
    # every verification round is kept: the draft as first checked, then the cut line
    assert [(c.text, [k.verdict for k in c.claims]) for c in out.checks] == [
        (TEXT, ["supported", "not_visible", "supported"]),
        ("A person opens a door.", ["supported", "supported"]),
    ]


def test_contradicted_claim_is_removed(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(("a person", "supported"), ("a red coat", "contradicted")),
            _supported("a person", "opens a door"),
        ],
        mend=[{"text": "A person opens a door."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert out.status == "verified" and "red" not in out.text


def _new_failure_each_round(*last_round, then=()):
    """A verifier that rejects a different detail of the line in each of the two repair
    rounds ("red", "coat": every line these tests verify says them), then answers
    `last_round` on the third check, then the answers in `then`."""
    answers = iter(
        [_claims(("a person", "supported"), (word, "not_visible")) for word in ("red", "coat")]
        + [_claims(*last_round), *then]
    )  # fmt: skip
    return lambda call: next(answers)


def test_contradicted_claim_never_kept_even_when_held(tmp_path, video):
    """After its two repairs the cue is held for a person, but a contradicted claim is still
    cut from its text; a claim that is only not visible stays for the reviewer to judge."""
    tf = FakeTF(
        verify=_new_failure_each_round(
            ("opens a door", "contradicted"),
            ("a person", "not_visible"),
            then=[_claims(("a person", "not_visible"))],
        ),  # fmt: skip
    )
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held"
    assert len(tf.of("verify")) == 4 and len(tf.of("mend")) == 3
    assert "opens" not in out.text and "door" not in out.text  # the contradiction is cut
    # the claims it is held with are those of the cut text, judged on it (one more check)
    assert _description(tf.of("verify")[-1]) == out.text == out.checks[-1].text
    assert [(c.text, c.verdict) for c in out.claims] == [("a person", "not_visible")]
    assert "not verified after 2 repairs" in out.note and "opens a door" in out.note
    assert speech.calls == []
    assert (out.audio_path, out.audio_dur) == (None, None)


def test_claim_rejected_again_after_removal_is_held(tmp_path, video):
    tf = FakeTF(
        verify=lambda call: _claims(("a person", "supported"), ("a red coat", "not_visible")),
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]))
    assert tf.roles() == ["verify", "mend", "verify"]
    assert out.status == "held" and "red" not in out.text
    assert "rejected a removed claim again: a red coat" in out.note


def test_a_mend_that_puts_the_failed_words_back_is_not_taken(tmp_path, video):
    """The mend answers with the line as it was: a cut word would come back, so the engine's
    own cut stands; the failed fact is gone."""
    tf = FakeTF(
        verify=[
            _claims(("a person", "supported"), ("a red coat", "not_visible")),
            _supported("a person", "opens a door"),
        ],  # fmt: skip
        mend=lambda call: {"text": TEXT},
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "mend", "verify"]
    assert "red" not in out.text and "coat" not in out.text
    assert out.text == _description(tf.of("verify")[1])


def test_contradiction_that_cannot_be_cut_withholds_the_text(tmp_path, video):
    """The contradicted claim's words all belong to a claim that passed: there is nothing to
    cut without cutting what passed, so the held cue withholds its text."""
    tf = FakeTF(
        verify=[_claims(("a person in a red coat", "supported"), ("red coat", "contradicted"))],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]))
    assert tf.roles() == ["verify"]
    assert out.status == "held" and out.text == ""
    assert "withheld" in out.note
    assert out.claims == []  # nothing is said, so no claim is published; the check is kept
    assert [k.verdict for k in out.checks[-1].claims] == ["supported", "contradicted"]


def test_everything_removed_is_held(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(("a person in a red coat", "not_visible"), ("opens a door", "not_visible"))
        ],
        mend=[{"text": "  "}],
    )
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held" and out.text == ""
    assert "nothing left" in out.note
    assert speech.calls == []


def test_removed_on_screen_text_is_not_demanded_back(tmp_path, video):
    """A quoted sign the frames don't show is cut; R19 must not then ask for it back."""
    ctx = CTX.model_copy(update={"verbatim": ["EXIT"]})
    tf = FakeTF(
        verify=[
            _claims(('a sign reads "EXIT"', "not_visible"), ("a person opens a door", "supported")),
            _supported("a person opens a door"),
        ],
        mend=[{"text": "A person opens a door."}],
    )
    cue = _cue('A sign reads: "EXIT". A person opens a door.')
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=cue, ctx=ctx)
    assert tf.roles() == ["verify", "mend", "verify"]
    assert out.status == "verified" and out.text == "A person opens a door."


# -- (c) a little too long: speed up -------------------------------------------------------


def test_audio_five_percent_over_gets_tempo(tmp_path, tone_slides):
    tf = FakeTF(verify=[_supported("a person", "opens a door")])
    out = _run(tmp_path, tone_slides, tf, FakeSpeech([4.2]))  # usable 4.0 s
    assert out.status == "verified"
    assert out.tempo == 1.05
    assert Path(out.audio_path) == tmp_path / "work" / "audio" / "c001.t.wav"
    assert out.audio_dur <= 4.0
    assert out.audio_dur == pytest.approx(4.0, abs=0.05)  # measured, not computed
    assert out.end == pytest.approx(out.start + out.audio_dur)
    assert tf.roles() == ["verify"]


# -- (d) far too long: compress, then go round again ----------------------------------------


def test_audio_forty_percent_over_compresses(tmp_path, video, monkeypatch):
    seen = []
    real = fin.compress

    def spy(text, target_words, keep, tf, *, run_id):
        seen.append((text, target_words, keep))
        return real(text, target_words, keep, tf, run_id=run_id)

    monkeypatch.setattr(fin, "compress", spy)
    long_text = "A person in a long red coat opens the wooden door of the shed."  # 14 words
    tf = FakeTF(
        verify=[_supported("a person", "red coat", "opens a door")] * 2,
        compress=[{"text": "A person in a red coat opens a door."}],  # 9 words
    )
    speech = FakeSpeech([5.6, 3.0])  # usable 4.0 s: 40 % over
    out = _run(tmp_path, video, tf, speech, cue=_cue(long_text))
    assert seen == [(long_text, 9, [])]  # floor(14 * 4.0 / 5.6 * 0.95)
    assert tf.roles() == ["verify", "compress", "verify"]
    cmp_call = tf.of("compress")[0]
    assert (cmp_call["model"], cmp_call["stage"], cmp_call["thinking"]) == (
        LIGHTNING, "finalize", False,
    )  # fmt: skip
    assert out.status == "verified"
    assert out.text == "A person in a red coat opens a door."
    assert out.audio_dur == pytest.approx(3.0, abs=1e-3)
    assert "compressed 14→9 words" in out.note
    assert [c["text"] for c in speech.calls] == [long_text, out.text]


def test_compress_keeps_on_screen_text_the_cue_reads(tmp_path, video, monkeypatch):
    seen = []

    def fake_compress(text, target_words, keep, tf, *, run_id):
        seen.append(keep)
        return 'A sign reads: "EXIT".'

    monkeypatch.setattr(fin, "compress", fake_compress)
    ctx = CTX.model_copy(update={"verbatim": ["EXIT", "Water use by sector\n2.1 million litres"]})
    tf = FakeTF(verify=lambda call: _supported("a sign reads EXIT"))
    cue = _cue('A sign reads: "EXIT". A person in a long red coat opens the wooden door.')
    out = _run(tmp_path, video, tf, FakeSpeech([8.0, 2.0]), cue=cue, ctx=ctx)
    assert seen == [["EXIT"]]
    assert out.status == "verified"


def test_never_fits_is_held_after_four_rounds(tmp_path, video):
    """Each compression shortens the line, but never enough: held after the fourth round."""
    tf = FakeTF(
        verify=lambda call: _supported(_description(call)),
        compress=lambda call: {"text": " ".join(_description(call).split()[:-2]) + "."},
    )
    speech = FakeSpeech(lambda text: 8.0)
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held"
    assert len(speech.calls) == 4 and len(tf.of("compress")) == 3
    assert "does not fit" in out.note
    assert (out.audio_path, out.audio_dur, out.end) == (None, None, None)


# -- a compression is never longer ------------------------------------------------------------


def test_a_compression_not_shorter_keeps_a_one_sentence_line_and_holds_it(tmp_path, video):
    """A rewrite with as many words or more is never taken (live: 13 -> 15 words, then four
    rounds lost). A one-sentence line has nothing else to give: held, its text as it was."""
    longer = "A person in a long bright red coat slowly opens the old wooden door."
    tf = FakeTF(verify=lambda call: _supported("a person opens a door"),
                compress=[{"text": longer}])  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([8.0]))
    assert out.status == "held" and out.text == TEXT
    assert "compression did not shorten the line" in out.note
    assert tf.roles() == ["verify", "compress"]


def test_a_compression_not_shorter_drops_the_lowest_priority_sentence(tmp_path, video):
    """With two visuals, the sentence of the one the plan rates lower goes instead, and the
    shorter line is checked and fitted again."""
    tf = FakeTF(
        verify=lambda call: _supported(*[s.rstrip(".") for s in _description(call).split(". ")]),
        compress=[{"text": HASHTAG + " It glows."}],  # longer than before
    )
    ctx = CTX.model_copy(update={"verbatim": ["#OPENDAY"],
                                 "priority": {"e001": 4, "e002": 2}})  # fmt: skip
    cue = _cue(HASHTAG, event_ids=["e001", "e002"], parts={
        "e001": 'A banner reads: "#OPENDAY".',
        "e002": "The school crest and motto appear on a blue background.",
    })  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([5.0, 2.0]), cue=cue,
               events={"e001": EV, "e002": LOGO}, ctx=ctx)  # fmt: skip
    assert out.status == "verified" and out.text == 'A banner reads: "#OPENDAY".'
    assert "dropped the sentence for e002" in out.note
    assert out.described == ["e001"]


def test_a_compression_that_fails_lint_is_not_taken(tmp_path, video):
    """A shorter rewrite that breaks a style rule gets one repair; still breaking it, it is
    not taken."""
    tf = FakeTF(verify=lambda call: _supported("a person opens a door"),
                compress=[{"text": "I see a person open a door."}],
                repair=[{"text": "I see a person open a door."}])  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([8.0]))
    assert out.status == "held" and out.text == TEXT
    assert "the shorter compression broke R7-person even after a repair" in out.note
    assert tf.roles() == ["verify", "compress", "repair"]


# -- (d2) a compression that cuts too much is tried once more, then held ---------------------

LOGO = EV.model_copy(update={"id": "e002", "t_start": 3.0, "t_end": 6.0, "text": "A logo."})
# a 14-word line (target 10) that a compression once answered with its hashtag alone
HASHTAG = 'A banner reads: "#OPENDAY". The school crest and motto appear on a blue background.'


def _two_visuals(tmp_path, video, tf, speech):
    ctx = CTX.model_copy(update={"verbatim": ["#OPENDAY"]})
    cue = _cue(HASHTAG, event_ids=["e001", "e002"])
    events = {"e001": EV, "e002": LOGO}
    return _run(tmp_path, video, tf, speech, cue=cue, events=events, ctx=ctx)


def test_a_compression_that_guts_the_line_is_retried_with_a_note(tmp_path, video):
    shorter = 'A banner reads: "#OPENDAY". The school crest appears.'  # 8 words
    tf = FakeTF(
        verify=[_supported("a banner reads #OPENDAY", "the school crest")] * 2,
        compress=[{"text": "#OPENDAY."}, {"text": shorter}],
    )
    out = _two_visuals(tmp_path, video, tf, FakeSpeech([5.0, 3.5]))  # usable 4.0 s
    assert tf.roles() == ["verify", "compress", "compress", "verify"]
    first, retry = (_user_text(c) for c in tf.of("compress"))
    assert "about 10 words" in first and "previous answer" not in first
    assert 'previous answer "#OPENDAY." kept 1 of about 10 words' in retry
    assert out.status == "verified" and out.text == shorter
    assert "compressed 14→8 words" in out.note


def test_a_compression_that_drops_a_visual_twice_is_held_with_the_full_line(tmp_path, video):
    one = 'A black screen shows the banner "#OPENDAY".'  # 7 words, 1 sentence
    tf = FakeTF(
        verify=[_supported("a banner reads #OPENDAY", "the school crest")],
        compress=[{"text": one}, {"text": one}],
    )
    out = _two_visuals(tmp_path, video, tf, FakeSpeech([5.0]))
    assert tf.roles() == ["verify", "compress", "compress"]
    assert "left 1 sentence for 2 visuals" in _user_text(tf.of("compress")[1])
    assert out.status == "held" and out.text == HASHTAG  # the reviewer gets the whole line
    assert "compression left 1 sentence for 2 visuals, twice" in out.note
    assert [c.verdict for c in out.claims] == ["supported", "supported"]  # they judged HASHTAG
    assert (out.audio_path, out.audio_dur) == (None, None)


def test_compress_sends_the_note_only_when_given():
    tf = FakeTF(compress=[{"text": "A door opens."}] * 2)
    compress("A red door opens slowly.", 3, [], tf, run_id="r1")
    compress("A red door opens slowly.", 3, [], tf, run_id="r1", note="it dropped a visual")
    plain, noted = (_user_text(c) for c in tf.calls)
    assert "it dropped a visual" not in plain and "it dropped a visual" in noted


# -- (d3) a claim cut that drops a sentence its claims did not cover is tried once more --------

TABBY = EV.model_copy(update={"id": "e002", "t_start": 2.5, "t_end": 6.0, "text": "A cat runs."})
COAT_CAT = "A person in a red coat opens a door. A cat runs past."
COAT_CLAIMS = _claims(
    ("a person opens a door", "supported"),
    ("in a red coat", "not_visible"),
    ("a cat runs past", "supported"),
)


def _coat_cat(tmp_path, video, tf, speech):
    cue = _cue(COAT_CAT, event_ids=["e001", "e002"])
    return _run(tmp_path, video, tf, speech, cue=cue, events={"e001": EV, "e002": TABBY})


def test_a_claim_cut_never_drops_another_visual(tmp_path, video):
    """The cut takes only the failed claim's tokens: the other visual's sentence stays."""
    tf = FakeTF(verify=[COAT_CLAIMS, _supported("a person opens a door", "a cat runs past")])
    out = _coat_cat(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "mend", "verify"]
    assert out.status == "verified" and "A cat runs past." in out.text and "red" not in out.text
    assert "1 claim removed: in a red coat" in out.note


def test_a_claim_cut_may_drop_the_sentence_its_claims_were_about(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(("a person opens a door", "supported"), ("a cat runs past", "not_visible")),
            _supported("a person opens a door"),
        ],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "mend", "verify"]
    assert out.status == "verified" and "cat" not in out.text


def test_mend_is_given_the_cut_line_and_the_cut_words():
    from lull.stages.verify import mend

    tf = FakeTF(mend=[{"text": "A door opens."}])
    assert mend("A door opens.", ["red"], tf, run_id="r1") == "A door opens."
    (call,) = tf.calls
    assert call["role"] == "mend" and call["model"] == LIGHTNING
    assert "<cut>\nred\n</cut>" in _user_text(call) and _description(call) == "A door opens."


# -- (e) verification never passes ----------------------------------------------------------


def test_verify_always_fails_is_held_after_two_repairs(tmp_path, video):
    """A new claim fails in every check; two are cut, and the third failure holds the cue:
    repairs never loop."""
    tf = FakeTF(
        verify=_new_failure_each_round(("a person", "supported"), ("door", "not_visible")),
    )
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held"
    assert len(tf.of("verify")) == 3 and len(tf.of("mend")) == 2
    assert "not verified after 2 repairs: door (not visible)" in out.note
    assert "2 claims removed: red; coat" in out.note
    assert speech.calls == []
    assert out.start == GAP.usable_start
    assert {c["stage"] for c in tf.calls} == {"finalize"}


def test_a_judge_that_gives_no_verdict_leaves_the_line_unverified(tmp_path, video):
    """The vision model answered with no verdict at all: every claim of the line counts as
    not visible, its words are cut, and nothing is left to voice."""
    tf = FakeTF(
        split=[
            _split(("a person in a red coat opens a door", "A person in a red coat opens a door"))
        ],
        verify=[{"verdicts": []}],
    )
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held" and out.text == "" and speech.calls == []
    assert [(k.text, k.verdict) for k in out.checks[-1].claims] == [
        ("a person in a red coat opens a door", "not_visible")
    ]  # fmt: skip


# -- lint ---------------------------------------------------------------------------------


def test_lint_error_is_repaired(tmp_path, video):
    bad = "We watch a person who appears to open a door."
    tf = FakeTF(
        repair=[{"text": "A person opens a door."}],
        verify=[_supported("a person opens a door")],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(bad))
    assert tf.roles() == ["repair", "verify"]
    rep = tf.of("repair")[0]
    assert (rep["model"], rep["stage"], rep["thinking"]) == (LIGHTNING, "finalize", False)
    assert rep["schema"] == TEXT_SCHEMA
    listed = _user_text(rep)
    assert "R7-person" in listed and "R12-hedge" in listed
    assert out.status == "verified" and out.text == "A person opens a door."


def test_lint_error_that_survives_repair_is_held(tmp_path, video):
    bad = "We watch a person open a door."
    tf = FakeTF(repair=[{"text": bad}])
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech, cue=_cue(bad))
    assert out.status == "held"
    assert tf.roles() == ["repair"] and speech.calls == []
    assert "R7-person" in out.note
    assert any(f["rule"] == "R7-person" and f["severity"] == "error" for f in out.findings)


def test_lint_warnings_are_kept_but_do_not_block(tmp_path, video):
    tf = FakeTF(verify=[_supported("a man", "opens a door")])
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue("A man opens a door."))
    assert out.status == "verified"
    assert tf.roles() == ["verify"]
    assert {"rule": "R14-gender", "severity": "warning"}.items() <= out.findings[0].items()
    assert Cue.model_validate_json(out.model_dump_json()) == out


def test_on_screen_text_not_read_by_the_cue_does_not_block(tmp_path, video):
    """A slide's full text is in the inventory; the cue reads only its title (plan rule 8)."""
    ctx = CTX.model_copy(update={"verbatim": ["Water use by sector\n2.1 million litres"]})
    tf = FakeTF(verify=[_supported("a slide reads Water use by sector")])
    cue = _cue("A slide reads: Water use by sector.")
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=cue, ctx=ctx)
    assert out.status == "verified" and tf.roles() == ["verify"]


# -- first mention of a cast name -------------------------------------------------


def test_first_mention_gets_the_descriptor(tmp_path, video):
    ctx = CTX.model_copy(update={"cast": {"Proog": PROOG}, "named": {"Proog"}})
    tf = FakeTF(verify=[_supported("a bald person", "in a dark coat", "climbs the ladder")])
    cue = _cue("Proog climbs the ladder.")
    out = _run(tmp_path, video, tf, FakeSpeech([3.0]), cue=cue, ctx=ctx)
    want = "A bald person in a dark coat, Proog, climbs the ladder."
    assert _description(tf.calls[0]) == want
    assert "Proog" in _user_text(tf.calls[0]).split("<names>", 1)[1]
    assert out.status == "verified" and out.text == want
    assert "Proog" in out.note
    assert names_in(out.text, {"Proog", "Emo"}) == {"Proog"}


def test_first_mention_skipped_when_already_introduced(tmp_path, video):
    ctx = CTX.model_copy(
        update={"cast": {"Proog": PROOG}, "named": {"Proog"}, "introduced": {"Proog"}}
    )
    tf = FakeTF(verify=[_supported("climbs the ladder")])
    cue = _cue("Proog climbs the ladder.")
    out = _run(tmp_path, video, tf, FakeSpeech([3.0]), cue=cue, ctx=ctx)
    assert _description(tf.calls[0]) == "Proog climbs the ladder."
    assert out.text == "Proog climbs the ladder." and out.note is None


def test_possessive_first_mention_is_introduced_by_repair(tmp_path, video):
    ctx = CTX.model_copy(update={"cast": {"Proog": PROOG}, "named": {"Proog"}})
    fixed = "The hand of a bald person in a dark coat, Proog, shakes."
    tf = FakeTF(repair=[{"text": fixed}], verify=[_supported("a hand shakes")])
    out = _run(tmp_path, video, tf, FakeSpeech([3.0]), cue=_cue("Proog's hand shakes."), ctx=ctx)
    assert tf.roles() == ["repair", "verify"]
    assert 'descriptor: "a bald person in a dark coat"' in _user_text(tf.calls[0])
    assert out.status == "verified" and out.text == fixed


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Proog climbs.", "{D}, Proog, climbs."),
        ("Then Proog climbs.", "Then {d}, Proog, climbs."),
        ("A door opens. Proog climbs.", "A door opens. {D}, Proog, climbs."),
        ("A door opens for Proog.", "A door opens for {d}, Proog."),
        ("Proog climbs. Proog waves.", "{D}, Proog, climbs. Proog waves."),
        ("In a moment, Proog climbs.", "In a moment, {d}, Proog, climbs."),
    ],
)
def test_introduce_names_inserts_once(text, want):
    want = want.format(D="A bald person in a dark coat", d=PROOG)
    out, introduced, inserted = introduce_names(text, {"Proog": PROOG}, set())
    assert out == want and introduced == {"Proog"} and inserted == ["Proog"]


def test_introduce_names_leaves_alone():
    cast = {"Proog": "the bald person in the dark coat"}
    done = "The bald person in the dark coat, Proog, climbs."
    assert introduce_names(done, cast, set()) == (done, {"Proog"}, [])
    quoted = 'A caption reads: "Proog". Proog climbs.'
    assert introduce_names(quoted, cast, set()) == (quoted, {"Proog"}, [])
    assert introduce_names("Proog's hand shakes.", cast, set()) == (
        "Proog's hand shakes.",
        set(),
        [],
    )
    assert introduce_names("Proog climbs.", cast, {"Proog"}) == ("Proog climbs.", set(), [])
    assert introduce_names("The proog climbs.", cast, set()) == ("The proog climbs.", set(), [])


# -- look-ahead prefixes ------------------------------------------------------------------

CAR = EV.model_copy(update={"id": "e002", "t_start": 6.5, "t_end": 9.0, "text": "A car passes."})
AHEAD_EVENTS = {"e001": EV, "e002": CAR}
AHEAD_TEXT = "A person waves. In a moment, a red car drives past."  # 11 words


def _ahead_cue(text=AHEAD_TEXT, ids=("e001", "e002")) -> Cue:
    return _cue(text, event_ids=list(ids), ahead=True)


def test_compression_that_drops_the_prefix_gets_it_restored(tmp_path, video):
    tf = FakeTF(
        verify=lambda call: _supported("a person waves", "a red car passes"),
        compress=[{"text": "A person waves. A red car passes."}],
        repair=[{"text": "A person waves. In a moment, a red car passes."}],
    )
    speech = FakeSpeech([6.0, 3.0])  # usable 4.0 s
    out = _run(tmp_path, video, tf, speech, cue=_ahead_cue(), events=AHEAD_EVENTS)
    assert tf.roles() == ["verify", "compress", "repair", "verify"]
    asked = _user_text(tf.of("repair")[0])
    assert "R2-ahead-format" in asked and "In a moment, a red car drives past." in asked
    assert out.status == "verified" and out.ahead is True
    assert out.text == "A person waves. In a moment, a red car passes."


def test_compression_that_drops_the_prefix_for_good_is_held(tmp_path, video):
    tf = FakeTF(
        verify=lambda call: _supported("a person waves", "a red car passes"),
        compress=[{"text": "A person waves. A red car passes."}],
        repair=[{"text": "A person waves. A red car passes."}],
    )
    speech = FakeSpeech([6.0])
    out = _run(tmp_path, video, tf, speech, cue=_ahead_cue(), events=AHEAD_EVENTS)
    assert tf.roles() == ["verify", "compress", "repair"]
    assert out.status == "held" and "In a moment" in out.note
    assert out.ahead is True  # a held cue keeps its placement
    assert len(speech.calls) == 1


def test_losing_one_of_two_prefixes_is_held(tmp_path, video):
    sign = EV.model_copy(update={"id": "e003", "t_start": 7.0, "t_end": 10.0})
    events = {"e002": CAR, "e003": sign}
    text = "In a moment, a red car drives past. In a moment, a sign lights up."
    tf = FakeTF(
        verify=lambda call: _supported("a red car drives past", "a sign lights up"),
        compress=[{"text": "In a moment, a red car passes. A sign lights up."}],
        repair=[{"text": "In a moment, a red car passes. A sign lights up."}],
    )
    cue = _ahead_cue(text, ids=("e002", "e003"))
    out = _run(tmp_path, video, tf, FakeSpeech([6.0]), cue=cue, events=events)
    assert tf.roles() == ["verify", "compress", "repair"]
    assert out.status == "held" and "In a moment" in out.note


def test_removing_a_whole_look_ahead_sentence_is_fine(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(("a person waves", "supported"), ("a red car drives past", "not_visible")),
            _supported("a person waves"),
        ],
        mend=[{"text": "A person waves."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_ahead_cue(), events=AHEAD_EVENTS)
    assert out.status == "verified" and out.text == "A person waves."
    assert out.ahead is False and "look-ahead sentence removed" in out.note


def test_a_mend_that_strips_the_look_ahead_but_keeps_its_sentence_is_held(tmp_path, video):
    tf = FakeTF(
        verify=[_claims(("a person waves", "not_visible"), ("a red car drives past", "supported"))],
        mend=[{"text": "A red car drives past."}],  # the look-ahead dropped, its sentence kept
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]), cue=_ahead_cue(), events=AHEAD_EVENTS)
    assert tf.roles() == ["verify", "mend"]
    assert out.status == "held" and "In a moment" in out.note and out.ahead is True


def test_split_sentences_and_count_ahead():
    t = 'A sign reads: "Exit. Now." In a moment, a door opens. It is 2.1 m wide.'
    assert [t[s:e] for s, e in split_sentences(t)] == [
        'A sign reads: "Exit. Now."', "In a moment, a door opens.", "It is 2.1 m wide.",
    ]  # fmt: skip
    assert split_sentences("  No mark at the end ") == [(2, 20)]
    assert count_ahead(t) == 1
    assert count_ahead('A caption reads: "In a moment, rain."') == 0  # quoted text is not a cue


# -- sentence timing from the measured audio ---------------------------------------------------

CAT = EV.model_copy(update={"id": "e002", "t_start": 2.2, "t_end": 3.0, "text": "A cat runs."})


def test_sentence_that_would_start_after_its_visual_is_compressed(tmp_path, video, monkeypatch):
    seen = []
    real = fin.compress

    def spy(text, target_words, keep, tf, *, run_id):
        seen.append(target_words)
        return real(text, target_words, keep, tf, run_id=run_id)

    monkeypatch.setattr(fin, "compress", spy)
    tf = FakeTF(
        verify=lambda call: _supported("a door opens", "a cat runs"),
        compress=[{"text": "A door opens. A cat runs."}],
    )
    cue = _cue("A person opens a door. A cat runs past.", event_ids=["e001", "e002"])
    # 3.6 s fits the 4.0 s pause, but sentence 2 (after 5 of 9 words) would start at
    # 2.0 + 3.6 x 5/9 = 4.0 s, after the cat leaves at 3.0 s
    speech = FakeSpeech([3.6, 1.5])
    out = _run(tmp_path, video, tf, speech, cue=cue, events={"e001": EV, "e002": CAT})
    assert seen == [4]  # floor(9 x (3.0 - 2.0) / (4.0 - 2.0) x 0.95)
    assert out.status == "verified" and out.text == "A door opens. A cat runs."
    assert out.audio_dur == pytest.approx(1.5)  # sentence 2 now starts at 2.75 s


def test_sentence_that_stays_late_is_held(tmp_path, video):
    tf = FakeTF(
        verify=lambda call: _supported("a door opens", "a cat runs"),
        compress=lambda call: {"text": _description(call)},
    )
    cue = _cue("A person opens a door. A cat runs past.", event_ids=["e001", "e002"])
    speech = FakeSpeech(lambda text: 3.6)
    out = _run(tmp_path, video, tf, speech, cue=cue, events={"e001": EV, "e002": CAT})
    # a compression that changes nothing is not taken, and no sentence has an event to own it
    assert out.status == "held" and len(speech.calls) == 1
    assert "sentence 2 would start at 4.00 s, after its visual ends at 3.00 s" in out.note
    assert "compression did not shorten the line" in out.note


def test_fit_is_measured_from_the_placed_start(tmp_path, video):
    tf = FakeTF(verify=[_supported("a person opens a door")])
    out = _run(tmp_path, video, tf, FakeSpeech([3.5]), cue=_cue(start=3.0))
    assert out.status == "verified" and out.start == 2.0 and out.tempo == 1.0
    assert tf.roles() == ["verify"]


def test_on_screen_text_the_failed_claim_does_not_hold_stays(tmp_path, video):
    """The cut takes only the failed claim's tokens: the sign's quoted text, which a claim
    that passed holds, is never lost with it (R19 has nothing to demand back)."""
    ctx = CTX.model_copy(update={"verbatim": ["EXIT"]})
    tf = FakeTF(
        verify=[
            _claims(
                ('a sign above the door reads: "EXIT"', "supported"),
                ("in a red coat", "not_visible"),
            ),
            _supported('a sign reads "EXIT"', "a person opens a door"),
        ],  # fmt: skip
    )
    cue = _cue('A sign above the door reads: "EXIT". A person in a red coat opens a door.')
    out = _run(tmp_path, video, tf, FakeSpeech([2.5]), cue=cue, ctx=ctx)
    assert tf.roles() == ["verify", "mend", "verify"]
    assert out.status == "verified" and '"EXIT"' in out.text and "red" not in out.text


def test_clip_uses_the_duration_from_the_context(tmp_path, video, monkeypatch):
    cuts = []

    def fake_cut(src, dst, start, end, *, height=480):
        cuts.append((start, end, height))
        Path(dst).write_bytes(b"\0" * 16)
        return Path(dst)

    monkeypatch.setattr(fin, "cut_clip", fake_cut)
    long = EV.model_copy(update={"t_start": 0.0, "t_end": 20.0})
    ctx = CTX.model_copy(update={"video_duration": 14.0})
    tf = FakeTF(verify=[_supported("a person opens a door")])
    _run(tmp_path, video, tf, FakeSpeech([2.0]), events={"e001": long}, ctx=ctx)
    assert cuts == [(0.0, 12.0, 360)]  # from the visual's start, within the 14 s video


# -- extended cues ----------------------------------------------------------------------------


def test_extended_cue_is_voiced_without_fitting(tmp_path, video):
    cue = _cue(status="extended", gap_id=None, id="x001")
    tf = FakeTF(verify=[_supported("a person", "opens a door")])
    out = _run(tmp_path, video, tf, FakeSpeech([9.0]), cue=cue, gap=None)
    assert out.status == "extended"
    assert out.start == 2.0 and out.tempo == 1.0
    assert out.audio_dur == pytest.approx(9.0, abs=1e-3)
    assert out.end == pytest.approx(11.0, abs=1e-3)
    assert tf.roles() == ["verify"]


def test_gap_must_match_the_cue(tmp_path, video):
    with pytest.raises(ValueError):
        _run(tmp_path, video, FakeTF(), FakeSpeech([]), gap=None)
    other = GAP.model_copy(update={"id": "g002"})
    with pytest.raises(ValueError):
        _run(tmp_path, video, FakeTF(), FakeSpeech([]), gap=other)
    with pytest.raises(ValueError):
        _run(tmp_path, video, FakeTF(), FakeSpeech([]), events={})


# -- clip window --------------------------------------------------------------------------------


def _e(t0, t1, id_="e1"):
    return EV.model_copy(update={"id": id_, "t_start": t0, "t_end": t1})


def test_clip_window_pads_and_clamps():
    assert clip_window([_e(2.0, 6.0)], 30.0) == (1.0, 7.0)
    assert clip_window([_e(0.5, 3.0)], 30.0) == (0.0, 4.0)
    assert clip_window([_e(27.0, 29.5)], 30.0) == (26.0, 30.0)
    assert clip_window([_e(2.0, 3.0, "a"), _e(5.0, 6.0, "b")], 30.0) == (1.0, 7.0)


def test_clip_window_capped_at_12_s_from_the_first_visual():
    assert clip_window([_e(0.0, 20.0)], 30.0) == (0.0, 12.0)
    assert clip_window([_e(10.0, 25.0)], 60.0) == (9.0, 21.0)
    assert clip_window([_e(0.0, 20.0)], 14.0) == (0.0, 12.0)
    assert clip_window([_e(0.0, 2.0)], 1.0) == (0.0, 1.0)


def test_the_verify_clip_covers_the_moment_a_line_describes():
    """However the line is placed (0.3 s before its visual, or an extended line whose picture
    holds after it), the clip is cut from the visuals: it covers a visual from its start, a
    long one from its start rather than its middle, and the start of every visual a line
    describes (up to CLIP_MAX_S)."""
    lo, hi = clip_window([_e(18.0, 25.0)], 60.0)
    assert lo <= 18.0 and hi >= 25.0
    lo, hi = clip_window([_e(30.0, 48.0)], 60.0)  # longer than the cap: its start is in
    assert lo <= 30.0 < hi
    lo, hi = clip_window([_e(10.0, 14.0, "a"), _e(22.0, 24.0, "b")], 60.0)
    assert lo <= 10.0 and hi >= 23.0


# -- model calls on their own -------------------------------------------------------------------


def test_a_cut_line_loses_the_failed_tokens_and_keeps_what_passed():
    from lull.stages.finalize import cut_tokens
    from lull.stages.verify import tokenize

    line = "A person in a red coat opens a door."
    assert [t.text for t in tokenize(line)][:6] == ["A", "person", "in", "a", "red", "coat"]
    assert cut_tokens(line, {2, 3, 4, 5}) == "A person opens a door."
    assert cut_tokens('A sign reads "\u6771\u4eac".', {2, 4}) == "A sign."  # no empty quotes
    assert cut_tokens("In a moment, a red door opens.", {4, 5, 6, 7}) == ""


def test_compress_passes_target_and_keep():
    tf = FakeTF(compress=[{"text": 'A sign reads: "EXIT".'}])
    out = compress('A tall sign above the door reads: "EXIT".', 4, ["EXIT"], tf, run_id="r1")
    assert out == 'A sign reads: "EXIT".'
    call = tf.calls[0]
    assert (call["model"], call["role"], call["stage"]) == (LIGHTNING, "compress", "finalize")
    user = _user_text(call)
    assert "4 words" in user and "EXIT" in user.split("<keep>", 1)[1]


# -- voice ---------------------------------------------------------------------------------------


def test_voice_measures_the_audio(tmp_path):
    path, dur = voice("A door opens.", "Sofia", FakeSpeech([2.5]), tmp_path / "a" / "c.wav")
    assert path == tmp_path / "a" / "c.wav" and dur == pytest.approx(2.5, abs=1e-3)


def test_atempo_speeds_up_within_limit(tmp_path):
    src = _wav(tmp_path / "s.wav", 4.2)
    out = atempo(src, tmp_path / "t.wav", 1.05)
    assert out == tmp_path / "t.wav"
    assert probe_audio(out) == pytest.approx(4.0, abs=0.05)
    for bad in (1.2, 0.9):
        with pytest.raises(ValueError):
            atempo(src, tmp_path / "x.wav", bad)


def probe_audio(path: Path) -> float:
    from lull.media import audio_duration

    return audio_duration(path)


# -- (f) the overlap gate -------------------------------------------------------------------


def _done(id_, start, dur, *, status="verified", gap_id="g"):
    return Cue(
        id=id_, gap_id=gap_id, start=start, end=start + dur, text="x", event_ids=[],
        status=status, ahead=False, claims=[], audio_path="a.wav", audio_dur=dur,
    )  # fmt: skip


def test_assert_no_overlap_flags_cue_crossing_speech():
    speech = [Span(start=0.0, end=1.8), Span(start=4.8, end=6.0)]
    with pytest.raises(OverlapError) as err:
        assert_no_overlap([_done("c001", 2.0, 3.0)], speech)
    assert err.value.cue_ids == ["c001"]
    assert_no_overlap([_done("c001", 2.0, 2.7)], speech)


def test_assert_no_overlap_ignores_held_and_extended():
    speech = [Span(start=2.0, end=6.0)]
    cues = [
        _done("c001", 2.0, 3.0, status="held"),
        _done("x001", 2.0, 3.0, status="extended", gap_id=None),
        _done("c002", 2.0, 3.0, gap_id=None),
    ]
    assert_no_overlap(cues, speech)
    for status in ("edited", "approved"):
        with pytest.raises(OverlapError):
            assert_no_overlap([_done("c003", 2.0, 3.0, status=status)], speech)


def test_assert_no_overlap_flags_two_overlapping_cues():
    with pytest.raises(OverlapError) as err:
        assert_no_overlap([_done("c002", 4.5, 1.5), _done("c001", 2.0, 3.0)], [])
    assert err.value.cue_ids == ["c001", "c002"]
    assert_no_overlap([_done("c001", 2.0, 2.5), _done("c002", 4.5, 1.5)], [])  # touching is fine


def test_overlap_report_numbers():
    cues = [
        _done("c001", 2.0, 3.0),  # [2.0, 5.0]
        _done("c002", 4.5, 1.5),  # [4.5, 6.0]: 0.5 s on c001, 0.5 s on speech
        _done("c003", 2.0, 9.0, status="held"),
    ]
    rep = overlap_report(cues, [Span(start=5.5, end=7.0)])
    assert rep["checked"] == 2
    assert rep["speech_s"] == pytest.approx(0.5)
    assert rep["between_cues_s"] == pytest.approx(0.5)
    assert rep["total_s"] == pytest.approx(1.0)
    assert rep["per_cue"] == {
        "c001": {"speech_s": 0.0, "cues_s": 0.5},
        "c002": {"speech_s": 0.5, "cues_s": 0.5},
    }
    assert rep["offending"] == ["c001", "c002"]
    clean = overlap_report([_done("c001", 2.0, 2.0)], [Span(start=5.5, end=7.0)])
    assert (clean["total_s"], clean["offending"]) == (0.0, [])


def test_overlap_gate_flags_sub_millisecond_overlap():
    rep = overlap_report([_done("c001", 2.0, 2.8004)], [Span(start=4.8, end=6.0)])
    assert rep["speech_s"] == 0.0 and rep["total_s"] == 0.0  # rounded for the report
    assert rep["offending"] == ["c001"]  # decided on the unrounded 0.4 ms
    with pytest.raises(OverlapError):
        assert_no_overlap([_done("c001", 2.0, 2.8004)], [Span(start=4.8, end=6.0)])


def test_overlap_gate_needs_audio():
    cue = _done("c001", 2.0, 3.0).model_copy(update={"audio_dur": None})
    with pytest.raises(ValueError):
        assert_no_overlap([cue], [])


# -- edge cases ------------------------------------------------------------------------------


def test_lost_ahead_follows_the_look_ahead_sentence_words_that_are_its_own():
    """A look-ahead merged into a longer unprefixed sentence is caught (most of its own words
    survive there), and words it shares with other sentences prove nothing."""
    merged = (
        "A woman enters with a lamp while light floods the hall and dust drifts as the door opens."
    )
    assert _lost_ahead("A door opens. In a moment, a woman enters with a lamp.", merged)
    assert not _lost_ahead("Two people walk. In a moment, two people jump.", "Two people walk.")
    assert _lost_ahead("Two people walk. In a moment, two people jump.", "Two people jump.")
    assert not _lost_ahead(
        "A door opens. In a moment, a woman enters.", "A door opens. In a moment, she enters."
    )


# -- which events a finished line still describes ---------------------------

EV2 = VisualEvent(
    id="e002", t_start=3.0, t_end=6.0, kind="action", text="A cat sleeps on the mat.",
    verbatim_text=None, salience=4, said_in_dialogue=False,
)  # fmt: skip


def test_a_line_that_lost_an_events_sentence_no_longer_describes_it(tmp_path, video):
    parts = {"e001": "A person opens a door.", "e002": "A cat sleeps on the mat."}
    cue = _cue("A person opens a door. A cat sleeps on the mat.", event_ids=["e001", "e002"],
               parts=parts)  # fmt: skip
    tf = FakeTF(
        verify=[
            _claims(
                ("a person opens a door", "supported"), ("a cat sleeps on the mat", "not_visible")
            ),
            _supported("a person opens a door"),
        ],  # fmt: skip
        mend=[{"text": "A person opens a door."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=cue,
               events={"e001": EV, "e002": EV2})  # fmt: skip
    assert out.status == "verified" and out.event_ids == ["e001", "e002"]
    assert out.described == ["e001"]


def test_described_events_follow_their_own_words_through_rewrites():
    parts = {"e1": "Two people walk.", "e2": "Two people jump over a fence."}
    assert described_events(parts, "Two people walk, then jump over a fence.") == ["e1", "e2"]
    assert described_events(parts, "Two people walk.") == ["e1"]  # shared words prove nothing
    assert described_events(parts, "They jump the fence.") == ["e2"]
    assert described_events({"e1": "A red door opens."}, "The door swings open.") == ["e1"]
    assert described_events({}, "Anything.") is None  # no parts: nothing to judge
    assert described_events(parts, "") == []


# -- verification clips and repeated claims -------------------------------------------------------


def test_the_verify_clip_is_at_least_4_s():
    """A 1.3 s fall gets 4 s of clip around it (padded, kept inside the video), still at
    most 12 s."""
    assert clip_window([_e(72.0, 73.3)], 90.0) == (70.65, 74.65)
    assert clip_window([_e(0.2, 0.8)], 30.0) == (0.0, 4.0)  # moved inside the video
    assert clip_window([_e(29.5, 29.9)], 30.0) == (26.0, 30.0)
    assert clip_window([_e(0.0, 1.0)], 2.0) == (0.0, 2.0)  # a 2 s video gives what it has
    assert clip_window([_e(0.0, 20.0)], 30.0) == (0.0, 12.0)  # the cap still holds


# -- edits and placement in the pause -------------------------------------------------------------


def test_refresh_described_follows_an_edited_text():
    """The Studio edit path: after a person changes the text, `described` is recomputed from
    the cue's parts, so coverage never counts an event the new text dropped."""
    parts = {"e001": "A person opens a door.", "e002": "A cat sleeps on the mat."}
    cue = _cue("A person opens a door. A cat sleeps on the mat.", event_ids=["e001", "e002"],
               parts=parts, described=["e001", "e002"], status="edited")  # fmt: skip
    edited = cue.model_copy(update={"text": "A cat sleeps on the mat."})
    fresh = refresh_described(edited)
    assert fresh.described == ["e002"] and edited.described == ["e001", "e002"]
    assert fresh.model_copy(update={"described": edited.described}) == edited  # nothing else
    assert refresh_described(_cue("Anything.", parts={})).described is None  # no parts


# -- where in its pause a cue starts ----------------------------------------------------------

CHART = VisualEvent(
    id="e009", t_start=18.0, t_end=25.0, kind="chart",
    text="A bar chart shows sales rising each year.", verbatim_text=None, salience=5,
    said_in_dialogue=False,
)  # fmt: skip
CHART_LINE = "A bar chart shows sales rising each year."


def _pause(us: float, ue: float) -> Gap:
    return Gap(id="g001", start=us - 0.2, end=ue + 0.2, usable_start=us, usable_end=ue,
               capacity_words=40)  # fmt: skip


def test_a_cue_waits_for_its_visual_within_its_pause(tmp_path, video):
    """A pause that opens 7.8 s before the chart: the line starts 0.3 s before the chart,
    not at the start of the pause."""
    gap = _pause(10.2, 25.0)
    cue = _cue(CHART_LINE, start=10.2, event_ids=["e009"])
    tf = FakeTF(verify=[_supported("a bar chart shows sales rising each year")])
    ctx = CTX.model_copy(update={"video_duration": 30.0})
    out = _run(tmp_path, video, tf, FakeSpeech([2.7]), cue=cue, gap=gap,
               events={"e009": CHART}, ctx=ctx)  # fmt: skip
    assert out.status == "verified" and out.ahead is False and out.text == CHART_LINE
    assert out.start == pytest.approx(17.7) and out.end == pytest.approx(20.4)


def test_a_cue_that_cannot_wait_starts_as_late_as_it_can_with_a_look_ahead(tmp_path, video):
    """Pause 10.2-19.0 s, chart from 18 s. Waiting for the chart would
    run into speech, so the line starts earlier, as late as the pause allows, and says so: it
    is voiced again with "In a moment, " and its fit checked again."""
    gap = _pause(10.2, 19.0)
    cue = _cue(CHART_LINE, start=10.2, event_ids=["e009"])
    tf = FakeTF(verify=[_supported("a bar chart shows sales rising each year")] * 2)
    speech = FakeSpeech([2.7, 3.7])
    ctx = CTX.model_copy(update={"video_duration": 30.0})
    out = _run(tmp_path, video, tf, speech, cue=cue, gap=gap, events={"e009": CHART}, ctx=ctx)
    assert [c["text"] for c in speech.calls] == [
        CHART_LINE, "In a moment, a bar chart shows sales rising each year."
    ]  # fmt: skip
    # the line changed after it was verified: it is verified again as it is voiced
    assert [_description(c) for c in tf.of("verify")] == [CHART_LINE, out.text]
    assert out.checks[-1].text == out.text
    assert out.status == "verified" and out.ahead is True
    assert out.text == "In a moment, a bar chart shows sales rising each year."
    assert out.start == pytest.approx(15.3) and out.end == pytest.approx(19.0)
    assert "look-ahead" in out.note


# -- the published claims are those of the final text -----------------------------------------


def test_a_compressed_cue_publishes_only_the_claims_its_final_text_makes(tmp_path, video):
    """A line compressed after it was verified is split and judged again, and the claims it
    carries are those of the final text, each judged on that text."""
    long_text = "A person in a long red coat opens the wooden door of the shed."  # 14 words
    final = "A person in a red coat opens a door."
    tf = FakeTF(
        split=[_split(("a person in a long red coat", "A person in a long red coat"),
                      ("the person opens the wooden door of the shed",
                       "opens the wooden door of the shed")),
               _split(("a person in a red coat", "A person in a red coat"),
                      ("the person opens a door", "opens a door"))],
        verify=[_verdicts(("k1", "supported"), ("k2", "supported")),
                _verdicts(("k1", "supported"), ("k2", "supported"))],
        compress=[{"text": final}],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([5.6, 3.0]), cue=_cue(long_text))
    assert out.status == "verified" and out.text == final
    assert [c.text for c in out.claims] == ["a person in a red coat", "the person opens a door"]
    last = out.checks[-1]
    assert last.text == out.text
    assert all(c in last.claims for c in out.claims)  # each verdict judged on the final text


def test_claims_of_the_text_tolerate_inflection_and_framing_words():
    from lull.stages.verify import said_in

    text = "Glycolysis and Krebs Cycle. A woman waves at two children."
    assert said_in("The slide is titled Glycolysis and Krebs Cycle.", text)
    assert said_in("a woman is waving", text)
    assert said_in("there are 2 children", text)
    assert said_in("there are three children", text) is False  # a different number
    assert said_in("The slide lists Glycolysis occurs in cytoplasm.", text) is False
    assert said_in("NADH and FADH2 are carriers", text) is False
    assert said_in("a slide is there", "A slide.")  # nothing but framing: the text says it


def test_a_name_read_off_the_screen_gets_no_descriptor_there():
    """Where the line reads the screen (capitals, or the text watch read) it names nobody:
    the descriptor goes to the first mention that is a name, if any."""
    cast = {"BAKE": "a baker", "Proog": PROOG}
    text, _, inserted = introduce_names("A card reads STEP 2: BAKE.", cast, set())
    assert (text, inserted) == ("A card reads STEP 2: BAKE.", [])
    text, _, inserted = introduce_names(
        "A banner reads Welcome, Proog. Proog waves.", cast, set(), ["Welcome, Proog"]
    )
    assert text == f"A banner reads Welcome, Proog. {PROOG[0].upper()}{PROOG[1:]}, Proog, waves."
    assert inserted == ["Proog"]


def test_a_placement_look_ahead_is_not_kept_through_compression(tmp_path, video, monkeypatch):
    """The line had to start early and got "In a moment, ", which made it too long for the
    pause. Compression shortens the line itself (its word target less the three prefix words),
    and placement decides again whether the shorter line needs the prefix. Asking a model to
    keep a prefix the engine added kept it at its old length round after round."""
    seen = []
    real = fin.compress

    def spy(text, target_words, keep, tf, *, run_id, note=None):
        seen.append((text, target_words))
        return real(text, target_words, keep, tf, run_id=run_id)

    monkeypatch.setattr(fin, "compress", spy)
    short = "A bar chart shows sales rising."
    tf = FakeTF(
        verify=lambda call: _supported(_description(call).removeprefix("In a moment, ")),
        compress=[{"text": short}],
    )
    speech = FakeSpeech(lambda text: 0.42 * len(text.split()))
    ctx = CTX.model_copy(update={"video_duration": 30.0})
    out = _run(tmp_path, video, tf, speech, cue=_cue(CHART_LINE, start=15.0, event_ids=["e009"]),
               gap=_pause(15.0, 19.0), events={"e009": CHART}, ctx=ctx)  # fmt: skip
    # 11 words prefixed: 4.62 s for a 4.0 s pause -> floor(11 x 4.0 / 4.62 x 0.95) = 9, less 3
    assert seen == [(CHART_LINE, 6)]
    assert out.status == "verified" and out.text == "In a moment, a bar chart shows sales rising."
    assert out.ahead is True and out.end <= 19.0 + 1e-6


def test_a_tempo_that_misses_by_the_padding_is_raised_once(tmp_path, video, monkeypatch):
    """atempo pads its output to whole frames, so the factor that should fit can miss by a few
    milliseconds (a 3.344 s line for a 3.32 s pause came out at 3.3215 s at 1.008): the factor
    is raised once rather than the line sent for compression."""
    factors = []

    def padded(src, dst, factor):
        factors.append(factor)
        return _wav(Path(dst), _wav_seconds(src) / factor + 0.004)

    monkeypatch.setattr(fin, "atempo", padded)
    tf = FakeTF(verify=[_supported("a person", "a red coat", "opens a door")])
    out = _run(tmp_path, video, tf, FakeSpeech([3.3437]), gap=_pause(2.0, 5.32))
    assert out.status == "verified" and out.audio_dur <= 3.32
    assert len(factors) == 2 and factors[0] < factors[1] <= 1.10
    assert out.tempo == factors[1] and "compress" not in tf.roles()


# -- a failed verdict is never filtered away --------------------------------------------------


def test_claims_of_drops_only_supported_claims_the_line_does_not_make():
    """Only the verifier's extras (supported claims about what else the frames show) are left
    out; every contradicted or not-visible claim stays, however it is worded."""
    from lull.stages.verify import claims_of

    text = "One person waves at the camera. Thirty students sit in rows."
    claims = [
        Claim(text="a person waves", verdict="supported", at=1.0),
        Claim(text="1 person", verdict="contradicted", at=1.0),  # "one" written as a digit
        Claim(text="30 students", verdict="not_visible", at=None),  # beyond the number words
        Claim(text="a dog sleeps by the door", verdict="supported", at=2.0),  # an extra
    ]
    assert [c.text for c in claims_of(text, claims)] == [
        "a person waves", "1 person", "30 students"
    ]  # fmt: skip


def test_a_paraphrased_contradicted_claim_is_never_voiced_as_verified(tmp_path, video):
    tf = FakeTF(
        verify=[_claims(("a person waves", "supported"), ("1 person", "contradicted"))],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue("One person waves at the camera."))
    assert out.status == "held" and out.audio_path is None
    # "1 person" holds no word the claim that passed does not: nothing can be cut
    assert tf.roles() == ["verify"]


def test_a_held_cue_keeps_a_contradiction_found_when_its_cut_text_is_checked_again(tmp_path, video):
    """The check of the cut text finds a new contradiction: the held cue keeps that claim
    with its verdict and says so, so a reviewer never sees the line as merely unconfirmed."""
    tf = FakeTF(
        verify=_new_failure_each_round(
            ("opens a door", "contradicted"),
            ("a person", "not_visible"),
            then=[_claims(("a person", "contradicted"))],
        ),  # fmt: skip
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]))
    assert out.status == "held" and "door" not in out.text
    assert [(c.text, c.verdict) for c in out.claims] == [("a person", "contradicted")]
    assert "a person (contradicted)" in out.note


def test_a_line_that_fails_its_check_as_voiced_keeps_the_contradiction(tmp_path, video):
    """The look-ahead was added after the line was verified; the check of the prefixed line
    contradicts a claim: held, and the claim stays with its verdict."""
    gap = _pause(10.2, 19.0)
    cue = _cue(CHART_LINE, start=10.2, event_ids=["e009"])
    tf = FakeTF(verify=[
        _supported("a bar chart shows sales rising each year"),
        _claims(("a bar chart", "supported"), ("sales rising each year", "contradicted")),
    ])  # fmt: skip
    ctx = CTX.model_copy(update={"video_duration": 30.0})
    out = _run(tmp_path, video, tf, FakeSpeech([2.7, 3.7]), cue=cue, gap=gap,
               events={"e009": CHART}, ctx=ctx)  # fmt: skip
    assert out.status == "held"
    assert ("sales rising each year", "contradicted") in [(c.text, c.verdict) for c in out.claims]


def test_the_first_sentence_gets_the_look_ahead_when_it_starts_early(tmp_path, video):
    """A two-event line whose second sentence already looks ahead: when placement starts it
    before its first visual (window 10-19 s, door from 17.5 s, line 3.6 s: it starts at
    15.4 s), the first sentence gets "In a moment, " too; the second one's says nothing about
    the first."""
    door = VisualEvent(id="e001", t_start=17.5, t_end=22.0, kind="action", text="A door opens.",
                       verbatim_text=None, salience=4, said_in_dialogue=False)  # fmt: skip
    line = "A door opens. In a moment, a bar chart shows sales."
    cue = _cue(line, start=10.0, event_ids=["e001", "e009"], ahead=True)
    tf = FakeTF(verify=lambda call: _supported("a door opens", "a bar chart shows sales"))
    ctx = CTX.model_copy(update={"video_duration": 30.0})
    out = _run(tmp_path, video, tf, FakeSpeech([3.6, 4.8]), cue=cue, gap=_pause(10.0, 19.0),
               events={"e001": door, "e009": CHART}, ctx=ctx)  # fmt: skip
    assert out.text == "In a moment, a door opens. In a moment, a bar chart shows sales."
    assert out.status == "verified" and out.ahead is True


# -- evaluation switch: verification off ----------------------------------------------------


def test_with_verification_off_a_line_is_voiced_unverified(tmp_path, video):
    """For the evaluation's ablation only: no claim is checked, the lint-clean fitted line is
    voiced with status "unverified" and carries no claim."""
    tf = FakeTF()  # no verify answer: a call would fail the test
    ctx = CTX.model_copy(update={"verify": False})
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), ctx=ctx)
    assert tf.calls == []
    assert out.status == "unverified" and out.claims == [] and out.checks == []
    assert out.audio_dur == pytest.approx(2.0, abs=1e-3)
    ext = _cue(TEXT, gap_id=None, status="extended")
    out = finalize_cue(ext, None, EVENTS, video, tmp_path / "w2", tf, FakeSpeech([3.0]), OPTS,
                       ctx, run_id="r1")  # fmt: skip
    assert out.status == "unverified" and out.gap_id is None


# -- atomic claims and a targeted repair ------------------------------------------------------


def test_a_cut_takes_only_the_clause_that_carries_a_failed_claim(tmp_path, video):
    """A two-clause line with one false clause keeps the true one."""
    line = "A person walks along the bridge while lasers scan the crowd."
    tf = FakeTF(
        split=[_split(("a person walks along the bridge", "A person walks along the bridge"),
                      ("lasers scan the crowd", "while lasers scan the crowd")),
               _split(("a person walks along the bridge", "A person walks along the bridge"))],
        verify=[_verdicts(("k1", "supported"), ("k2", "not_visible")),
                _verdicts(("k1", "supported"))],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(line))
    assert out.status == "verified" and out.text == "A person walks along the bridge."
    assert "<cut>\ncrowd, lasers, scan, the, while\n</cut>" in _user_text(tf.of("mend")[0])


def test_a_compression_that_breaks_a_style_rule_gets_one_repair(tmp_path, video):
    """A shorter rewrite that dropped the on-screen text the line reads is repaired once;
    shorter and lint-clean after the repair, it is taken."""
    ctx = CTX.model_copy(update={"verbatim": ["EXIT"]})
    line = 'A sign reads: "EXIT". A person in a long red coat opens the wooden door.'
    fixed = 'A sign reads: "EXIT". A person opens a door.'
    tf = FakeTF(
        verify=lambda call: _supported('a sign reads "EXIT"', "a person opens a door"),
        compress=[{"text": "A sign glows. A person opens a door."}],
        repair=[{"text": fixed}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([8.0, 2.0]), cue=_cue(line), ctx=ctx)
    assert tf.roles() == ["verify", "compress", "repair", "verify"]
    assert out.status == "verified" and out.text == fixed


def test_a_cut_that_leaves_only_the_look_ahead_leaves_nothing(tmp_path, video):
    """Live: a cut left "In a moment, ." and the cue was held for having no claim to check.
    A line with nothing but its look-ahead prefix says nothing: held as nothing left."""
    tf = FakeTF(
        verify=[_claims(("a screen reads MEMORY OVERRIDE", "not_visible"))],
        mend=[{"text": "In a moment, ."}],
    )
    cue = _cue("In a moment, a screen reads MEMORY OVERRIDE.", ahead=True)
    out = _run(tmp_path, video, tf, FakeSpeech([]), cue=cue)
    assert out.status == "held" and out.text == ""
    assert "nothing left once the unverified claims were removed" in out.note
    assert tf.roles() == ["verify"]  # nothing to mend


# -- claims split by the engine, judged by the vision model -----------------------------------


def _split(*claims):
    """A split answer: (text, words) pairs, ids k1, k2, ...; `words` are the line's words the
    claim comes from (a list of ranges when given as [[a, b], ...]), resolved to token ranges
    when the line is known (see _ranges)."""
    return {"claims": [{"id": f"k{i}", "text": t, "tokens": sp}
                       for i, (t, sp) in enumerate(claims, start=1)]}  # fmt: skip


_FINE = [("a person", "A person"), ("a person in a red coat", "A person in a red coat"),
         ("a person opens a door", [[0, 1], [6, 8]])]  # fmt: skip


def _verdicts(*items):
    """A judge answer: (id, verdict) pairs, or (id, verdict, at)."""
    return {"verdicts": [{"id": it[0], "verdict": it[1], "evidence": "seen",
                          "at": it[2] if len(it) > 2 else 1.0} for it in items]}  # fmt: skip


def test_a_sound_split_is_judged_claim_by_claim(tmp_path):
    """Lightning splits the line (role "split", thinking off, temperature 0, the split
    schema); the vision model judges exactly those claims by id. A claim it gives no verdict
    is not visible; an id it invents is ignored; times move to video time."""
    from lull.stages.verify import SPLIT_SCHEMA

    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\0" * 16)
    line = "A person in a red coat opens a door."
    tf = FakeTF(
        split=[
            _split(
                ("a person in a red coat", "A person in a red coat"),
                ("the person opens a door", "opens a door"),
            )
        ],
        verify=[_verdicts(("k1", "supported", 1.5), ("k9", "supported"))],
    )
    out = verify(line, clip, tf, run_id="r1", names={"Emo"}, offset=10.0)
    assert [(c.text, c.verdict, c.at, c.span) for c in out] == [
        ("a person in a red coat", "supported", 11.5, "A person in a red coat"),
        ("the person opens a door", "not_visible", None, "opens a door"),
    ]  # fmt: skip
    split, judged = tf.calls
    assert (split["role"], split["model"], split["thinking"], split["temperature"]) == (
        "split", LIGHTNING, False, 0.0)  # fmt: skip
    assert split["schema"] == SPLIT_SCHEMA and judged["schema"] == VERIFY_SCHEMA
    listed = _user_text(judged).split("<claims>", 1)[1]
    assert "k1: a person in a red coat" in listed and "k2: the person opens a door" in listed
    assert "Emo" in _user_text(judged).split("<names>", 1)[1].split("</names>", 1)[0]


@pytest.mark.strict_split
def test_a_split_whose_tokens_leave_words_out_is_asked_again_naming_them():
    from lull.stages.verify import check_split, split_claims

    line = "A person in a red coat opens a door."
    bad = [{"id": "k1", "text": "a person", "tokens": [[0, 40]]},  # past the last token
           {"id": "k2", "text": "opens a door", "tokens": "opens a door"}]  # fmt: skip
    good = _split(("a person in a red coat", "A person in a red coat"),
                  ("a person opens a door", [[0, 1], [6, 8]]))  # fmt: skip
    tf = FakeTF(split=[{"claims": bad}, good])
    claims = split_claims(line, tf, run_id="r1")
    assert [c.span(line) for c in claims] == [
        "A person in a red coat", "A person … opens a door"
    ]  # fmt: skip
    asked = _user_text(tf.calls[0])
    assert "<tokens>\n0: A\n1: person\n2: in" in asked
    retry = _user_text(tf.calls[1])
    assert "claim k1 has tokens outside 0..9" in retry
    assert "tokens no claim says: 1 'person', 2 'in', 4 'red', 5 'coat'" in retry
    assert check_split(line, claims) == []
    # names are context and framing words need no claim
    assert check_split("In a moment, Proog waves.", [
        _sc("k1", "Proog waves", "waves", "In a moment, Proog waves.")],
        names={"Proog"}) == []  # fmt: skip


def _sc(cid, text, words, line=""):
    """A split claim: its tokens given as ranges, or as the words of `line` it comes from."""
    from lull.stages.verify import SplitClaim

    tokens = words if isinstance(words, list) else _range_of(words, line)
    return SplitClaim(id=cid, text=text, tokens=[tuple(r) for r in tokens])


@pytest.mark.strict_split
def test_a_split_that_fails_twice_falls_back_to_one_claim_per_sentence(caplog):
    from lull.stages.verify import split_claims

    tf = FakeTF(split=[{"claims": []}, _split(("a door", "a door"))])
    with caplog.at_level("WARNING", logger="lull.stages.verify"):
        claims = split_claims("In a moment, a door opens. A cat runs past.", tf, run_id="r1")
    line = "In a moment, a door opens. A cat runs past."
    assert [(c.text, c.span(line)) for c in claims] == [
        ("a door opens", "a door opens."), ("A cat runs past", "A cat runs past."),
    ]  # fmt: skip
    assert "one claim per sentence" in caplog.text


def test_a_split_is_kept_per_text_so_a_line_checked_again_is_not_split_again():
    from lull.stages.verify import split_claims

    cache: dict = {}
    tf = FakeTF(split=[_split(("a door opens", "A door opens"))])
    first = split_claims("A door opens.", tf, run_id="r1", cache=cache)
    again = split_claims("A door opens.", tf, run_id="r1", cache=cache)
    assert first == again and len(tf.calls) == 1


def test_the_judge_needs_a_verdicts_list_and_keeps_the_worse_of_two(tmp_path):
    from lull.stages.verify import judge

    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\0" * 16)
    claims = [_sc("k1", "a cat sits", "A cat sits", "A cat sits.")]
    with pytest.raises(ValueError):
        judge("A cat sits.", claims, clip, FakeTF(split=[], verify=[{"claims": []}]),
              run_id="r1")  # fmt: skip
    tf = FakeTF(split=[], verify=[_verdicts(("k1", "supported"), ("k1", "contradicted"))])
    [c] = judge("A cat sits.", claims, clip, tf, run_id="r1")
    assert c.verdict == "contradicted"


@pytest.mark.strict_split
def test_one_contradicted_claim_of_three_is_cut_and_the_new_line_split_and_judged_again(
    tmp_path, video
):
    """Strict check, a realistic split: the contradicted claim's own tokens are cut (those a
    passed claim also holds stay), the mend may only tidy, and the shorter line is split
    afresh (here after one re-ask) and judged; only its claims are published."""
    line, cut = TEXT, "A person opens a door"
    tf = FakeTF(
        split=[_split(*_FINE),
               {"claims": [{"id": "k1", "text": "a person", "tokens": [[0, 1]]}]},  # no door
               _split(("a person", "A person"), ("a person opens a door", cut))],
        verify=[_verdicts(("k1", "supported"), ("k2", "contradicted"), ("k3", "supported")),
                _verdicts(("k1", "supported"), ("k2", "supported"))],
        mend=[{"text": "A person opens a door."}],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(line))
    assert tf.roles(splits=True) == ["split", "verify", "mend", "split", "split", "verify"]
    assert _description(tf.of("mend")[0]) == "A person opens a door."
    assert "<cut>\na, coat, in, red\n</cut>" in _user_text(tf.of("mend")[0])  # "A person" kept
    retry = _user_text(tf.of("split")[2])
    assert "tokens no claim says: 2 'opens', 4 'door'" in retry
    assert out.status == "verified" and out.text == "A person opens a door."
    assert [(c.text, c.verdict) for c in out.claims] == [
        ("a person", "supported"), ("a person opens a door", "supported")
    ]  # fmt: skip


def test_the_split_prompt_asks_for_one_checkable_fact_and_exact_spans():
    from lull.stages.verify import load_prompt

    text = " ".join(load_prompt("split").split())
    assert "One checkable fact per claim" in text
    for case in ('"and"', "lists", '"X does Y while Z"', "[first, last] inclusive"):
        assert case in text, case
    judge_prompt = " ".join(load_prompt("verify").split())
    assert "add no claim" in judge_prompt


# -- a second look never rescues a failed claim -------------------------------------------------


def test_supported_claims_the_line_does_not_make_are_left_out_and_noted(tmp_path, video,
                                                                       monkeypatch):  # fmt: skip
    from lull.stages import verify as ver

    extra = Claim(text="a dog sleeps on the porch", verdict="supported", at=1.0, span=None)
    kept = Claim(text="a person opens a door", verdict="supported", at=1.0,
                 span="A person in a red coat opens a door.")  # fmt: skip
    dropped: list = []
    assert ver.claims_of(TEXT, [kept, extra], dropped=dropped) == [kept]
    assert dropped == [extra]

    def fake_verify(text, clip, tf, *, dropped=None, **kw):
        if dropped is not None:
            dropped.append(extra)
        return [kept]

    monkeypatch.setattr(fin, "verify", fake_verify)
    out = _run(tmp_path, video, FakeTF(), FakeSpeech([2.0]))
    assert out.status == "verified"
    assert "left out a claim the line does not make: a dog sleeps on the porch" in out.note


# -- a look-ahead survives every rewrite ---------------------------------------------------------


def test_a_compression_repair_that_drops_the_look_ahead_is_not_taken(tmp_path, video):
    """The repair a shorter, lint-failing compression gets goes through the look-ahead check
    like every rewrite: when "In a moment, " is lost and not restored, it is not taken."""
    before = "A door opens. In a moment, a big bar chart shows the sales figures."
    tf = FakeTF(repair=[{"text": "A door opens. A chart shows sales."}] * 2)
    st = fin._Finalise(_cue(before, ahead=True), GAP, [EV], video, tmp_path / "w", tf,
                       FakeSpeech([]), OPTS, CTX, "r1")  # fmt: skip
    st.text = before
    got = st.shorter(before, "I see a door open. In a moment, a chart shows sales.", [])
    assert got is None
    assert tf.roles() == ["repair", "repair"]  # the second asks for the look-ahead back


def test_a_look_ahead_is_never_lost_to_a_compression_end_to_end(tmp_path, video):
    door = VisualEvent(id="e001", t_start=12.0, t_end=16.0, kind="action", text="A door opens.",
                       verbatim_text=None, salience=4, said_in_dialogue=False)  # fmt: skip
    line = "A door opens. In a moment, a big bar chart shows the sales figures rising."
    cue = _cue(line, start=10.0, event_ids=["e001", "e009"], ahead=True, parts={
        "e001": "A door opens.",
        "e009": "In a moment, a big bar chart shows the sales figures rising.",
    })  # fmt: skip
    tf = FakeTF(
        verify=lambda call: _supported("a door opens", "a chart shows sales"),
        compress=[{"text": "I see a door open. In a moment, a chart shows sales."}],
        repair=[{"text": "A door opens. A chart shows sales."}] * 2,
    )
    ctx = CTX.model_copy(update={"video_duration": 30.0})
    out = _run(tmp_path, video, tf, FakeSpeech([9.0, 3.0]), cue=cue, gap=_pause(10.0, 17.0),
               events={"e001": door, "e009": CHART}, ctx=ctx)  # fmt: skip
    assert not (out.status == "verified" and "In a moment" not in out.text)


def test_a_dropped_sentence_that_leaves_a_line_breaking_a_rule_is_not_taken(tmp_path, video):
    """Dropping the sentence that introduces a name would leave the name unintroduced (R15):
    the shorter line is linted like any rewrite, and here it is not taken."""
    line = "A tall person, Proog, waves. Proog opens the wooden door of the old shed."
    ctx = CTX.model_copy(update={"cast": {"Proog": "a tall person"}, "named": {"Proog"},
                                 "priority": {"e001": 2, "e002": 4}})  # fmt: skip
    cue = _cue(line, event_ids=["e001", "e002"], parts={
        "e001": "A tall person, Proog, waves.",
        "e002": "Proog opens the wooden door of the old shed.",
    })  # fmt: skip
    tf = FakeTF(verify=lambda call: _supported(_description(call)),
                compress=[{"text": line + " Slowly."}])  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([8.0]), cue=cue,
               events={"e001": EV, "e002": LOGO}, ctx=ctx)  # fmt: skip
    assert out.status == "held" and out.text == line
    assert "dropping the sentence for e001 breaks R15-name" in out.note


def test_the_verify_estimate_covers_the_longest_clip():
    """The spend check reserves enough for a clip of CLIP_MAX_S (20 s), not just the 12 s cap."""
    from lull.stages import verify as ver

    assert ver.VERIFY_ESTIMATE >= 0.003 * ver.CLIP_MAX_S / ver.CLIP_CAP_S


# -- the split check reads the claims, not just their spans ---------------------------------------


@pytest.mark.strict_split
@pytest.mark.parametrize(
    "line, claims, names, problem",
    [
        (
            "A person in a red coat opens a door.",
            [("a person opens a door", "A person in a red coat opens a door.")],
            (),
            "tokens no claim says: 2 'in', 4 'red', 5 'coat'",
        ),
        (
            'A sign reads: "EXIT 12".',
            [('a sign reads "EXIT"', 'A sign reads: "EXIT 12"')],
            (),
            "tokens no claim says: 6 '12'",
        ),
        (
            "A cat sits under the table without a collar.",
            [("a cat sits", "A cat sits"), ("the table", "the table"), ("a collar", "a collar")],
            (),
            "tokens no claim says: 3 'under', 6 'without'",
        ),
        (
            "Red Leader waves from a red car.",
            [("Red Leader waves from a", "Red Leader waves from a"), ("a car", "car")],
            ("Red Leader",),
            "tokens no claim says: 5 'red'",
        ),
        (
            "A person opens a door.",
            [("a person in a red coat opens a door", "A person opens a door")],
            (),
            "says coat, red, which its tokens do not",
        ),
        (
            "The lights go off. Both doors stay up.",
            [("the lights go", "The lights go"), ("doors stay", "doors stay")],
            (),
            "tokens no claim says: 3 'off', 5 'Both', 8 'up'",
        ),
        ("Nobody is in the room.", [("the room", "the room")], (), "0 'Nobody'"),
        (
            "The door does not open.",
            [("the door opens", "The door does not open")],
            (),
            "tokens no claim says: 2 'does', 3 'not'",
        ),
        (  # attributes swapped between claims: each claim says words its own tokens lack
            "A man in a red hat stands by a blue car.",
            [
                ("a man in a blue hat", "A man in a red hat"),
                ("a red car", "red hat stands by a blue car"),
                ("a man stands by a car", "stands by a blue car"),
            ],  # fmt: skip
            (),
            "claim k1 says blue, which its tokens do not",
        ),
    ],
    ids=[
        "dropped-fact",
        "dropped-number",
        "position-and-negation",
        "name-and-colour",
        "added-fact",
        "particles",
        "nobody",
        "negation",
        "binding-swap",
    ],
)
def test_the_split_check_reads_each_claims_own_words(line, claims, names, problem):
    from lull.stages.verify import check_split

    got = check_split(line, [_sc(f"k{i}", t, sp, line) for i, (t, sp) in enumerate(claims, 1)],
                      names)  # fmt: skip
    assert any(problem in p for p in got), got


@pytest.mark.strict_split
def test_a_binding_swap_is_named_for_every_claim_that_moves_a_word():
    from lull.stages.verify import check_split

    line = "A man in a red hat stands by a blue car."
    claims = [("a man in a blue hat", "A man in a red hat"),
              ("a red car", "red hat stands by a blue car"),
              ("a man stands by a car", "stands by a blue car")]  # fmt: skip
    got = check_split(line, [_sc(f"k{i}", t, sp, line) for i, (t, sp) in enumerate(claims, 1)])
    assert "claim k1 says blue, which its tokens do not" in got
    assert "claim k3 says man, which its tokens do not" in got


REAL = "In a moment, a woman in a red coat walks past two parked cars."


@pytest.mark.strict_split
@pytest.mark.parametrize(
    "claims, ok",
    [
        ([("a woman", "a woman"), ("a woman in a red coat", "a woman in a red coat"),
          ("a woman walks past two parked cars", "walks past two parked cars")], False),
        ([("a woman", "a woman"), ("a woman in a red coat", "a woman in a red coat"),
          ("walks past two parked cars", "walks past two parked cars")], True),
        ([("a woman in a red coat", "a woman in a red coat"),
          ("a woman walks past cars", [[4, 5], [10, 11], [14, 14]]),
          ("two cars are parked", "two parked cars")], True),
        ([("the woman wears a red coat", "a woman in a red coat"),
          ("the woman walks past two parked cars", [[4, 5], [10, 14]])], False),  # "wears"
        ([("a woman", "a woman"), ("the woman's coat is red", "a woman in a red coat"),
          ("the woman walks past two parked cars", [[4, 5], [10, 14]])], False),  # "in"
        ([("a woman in a red coat walks past two parked cars",
           "a woman in a red coat walks past two parked cars")], True),
        ([("a woman", "a woman"), ("a woman in a red coat", "a woman in a red coat"),
          ("a woman walked past two parked cars", [[4, 5], [10, 14]])], True),  # tense
    ],
    ids=["woman-not-in-its-tokens", "fine", "non-contiguous", "wears", "possessive",
         "one-claim", "tense"],
)  # fmt: skip
def test_a_realistic_split_is_checked_word_by_word(claims, ok):
    from lull.stages.verify import check_split

    got = check_split(REAL, [_sc(f"k{i}", t, sp, REAL) for i, (t, sp) in enumerate(claims, 1)])
    assert (got == []) is ok, got


def test_the_same_words_from_two_places_are_two_claims(tmp_path):
    """Two claims with the same wording but from different words of the line are judged
    each on their own; only a true repeat (same text, same span) folds into one."""
    from lull.stages.verify import judge

    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\0" * 16)
    line = "A door opens. A door opens."
    claims = [_sc("k1", "a door opens", [[0, 3]]), _sc("k2", "a door opens", [[4, 6]]),
              _sc("k3", "a door opens", [[4, 6]])]  # fmt: skip
    tf = FakeTF(split=[], verify=[_verdicts(("k1", "supported"), ("k2", "contradicted"),
                                            ("k3", "supported"))])  # fmt: skip
    got = judge(line, claims, clip, tf, run_id="r1")
    assert [(c.span, c.verdict) for c in got] == [
        ("A door opens.", "supported"), ("A door opens", "contradicted")
    ]  # fmt: skip


def test_a_line_whose_split_fell_back_says_so(tmp_path, video):
    tf = FakeTF(split=[{"claims": []}, {"claims": []}],
                verify=[_verdicts(("s1", "supported"))])  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert out.status == "verified"
    assert "claims: one per sentence (split failed)" in out.note


def test_the_judge_supports_a_claim_only_when_every_detail_is_visible():
    from lull.stages.verify import load_prompt

    assert "only when every detail in it is visible" in " ".join(load_prompt("verify").split())


# -- the engine cuts; nothing a second look or a mend says can bring a failed fact back ----------


@pytest.mark.strict_split
@pytest.mark.parametrize(
    "mended",
    [
        "A person in a red coat opens the door.",
        "A person in a red coat opens a door",
        "A person in a red coat pushes a door.",
        "A person in a bright red coat opens a door.",
        "A person wearing a red coat opens a door.",
    ],
    ids=["a-to-the", "full-stop", "opens-to-pushes", "word-inserted", "reworded"],
)
def test_a_mend_never_brings_a_cut_fact_back(tmp_path, video, mended):
    """Whatever the mend answers, the failed claim's words ("red coat") are out of the line:
    a mend that would add or put back a word is not taken, and the engine's own cut stands."""
    tf = FakeTF(
        split=[_split(*_FINE), _split(("a person opens a door", "A person opens a door"))],
        verify=[_verdicts(("k1", "supported"), ("k2", "contradicted"), ("k3", "supported")),
                _verdicts(("k1", "supported"))],
        mend=[{"text": mended}],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(TEXT))
    assert out.status in ("verified", "held")
    assert "red" not in out.text and "coat" not in out.text
    if out.status == "verified":
        assert out.text == "A person opens a door."


@pytest.mark.parametrize("verdict", ["not_visible", "contradicted"])
def test_a_whole_sentence_claim_that_fails_leaves_nothing(tmp_path, video, verdict):
    tf = FakeTF(
        split=[_split(("a person in a red coat opens a door", TEXT.rstrip(".")))],
        verify=[_verdicts(("k1", verdict)), _verdicts(("k1", "supported"))],
        mend=[{"text": "A person in a red coat opens the door."}],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(TEXT))
    assert out.status == "held" and out.text == "" and out.audio_path is None


@pytest.mark.strict_split
def test_a_fallen_back_split_that_fails_leaves_nothing(tmp_path, video):
    tf = FakeTF(
        split=[{"claims": []}, {"claims": []}],
        verify=[_verdicts(("s1", "not_visible"))],
        mend=[{"text": "A person in a red coat opens the door."}],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(TEXT))
    assert out.status == "held" and out.text == ""
    assert "claims: one per sentence (split failed)" in out.note


@pytest.mark.parametrize(
    "line, held, failed, mended, gone",
    [
        ("A man’s coat is red.", ("a man’s coat", [[0, 2]]),
         ("a man’s coat is red", [[0, 4]]), "A man's coat is red.", "red"),
        ('A sign reads "東京".', ("a sign", [[0, 1]]),
         ('a sign reads "東京"', [[0, 5]]), 'The sign reads "東京".',
         "東京"),
    ],
    ids=["apostrophe", "non-latin"],
)  # fmt: skip
def test_a_cut_works_on_tokens_whatever_the_script_or_apostrophe(
    tmp_path, video, line, held, failed, mended, gone
):
    """The failed claim's tokens are cut whatever the apostrophe or the script: a mend that
    only straightens the apostrophe or swaps the article cannot put them back."""
    tf = FakeTF(
        split=[{"claims": [{"id": "k1", "text": held[0], "tokens": held[1]},
                           {"id": "k2", "text": failed[0], "tokens": failed[1]}]},
               {"claims": [{"id": "k1", "text": held[0], "tokens": held[1]}]}],
        verify=[_verdicts(("k1", "supported"), ("k2", "contradicted")),
                _verdicts(("k1", "supported"))],
        mend=[{"text": mended}],
    )  # fmt: skip
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_cue(line))
    assert gone not in out.text
    assert out.status == "held" or out.text.rstrip(".") == line[: len(held[0])]


@pytest.mark.parametrize(
    "raw, mended, ok",
    [
        ("A person opens a red door.", "The person opens the door.", True),  # articles, a drop
        ("A person opens a red door.", "A person opens a red door", True),
        ("A person, opens a door.", "A person opens a door.", True),
        ("A person opens a red door.", "A person in red opens a door.", False),  # "red" moved
        ("A person opens a red door.", "A person opens a red red door.", False),  # doubled
        ("A person opens a door.", "A person in a bright coat opens a door.", False),  # added
        ("A man with a hat walks.", "A man without a hat walks.", False),  # negated
        ("A man’s hat.", "A man's hat.", True),  # an apostrophe straightened
    ],
)
def test_a_mend_may_tidy_but_never_add_or_move_a_word(raw, mended, ok):
    from lull.stages.verify import mend_keeps

    assert mend_keeps(raw, mended) is ok
