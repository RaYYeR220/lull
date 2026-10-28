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
    UNSPECIFIED,
    VERIFY_SCHEMA,
    clip_window,
    parse_claims,
    remove_claims,
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


class FakeTF:
    """Answers by role: a list of canned answers, or a function of the call."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls: list[dict] = []

    def json(self, model, messages, schema, **kw) -> dict:
        call = {"model": model, "messages": messages, "schema": schema, **kw}
        self.calls.append(call)
        a = self.answers[kw["role"]]
        return a(call) if callable(a) else a.pop(0)

    def roles(self) -> list[str]:
        return [c["role"] for c in self.calls]

    def of(self, role: str) -> list[dict]:
        return [c for c in self.calls if c["role"] == role]


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
    call = tf.calls[0]
    assert call["model"] == VL and call["schema"] == VERIFY_SCHEMA
    assert (call["stage"], call["thinking"], call["temperature"]) == ("finalize", False, 0.2)
    assert (call["max_tokens"], call["estimate"]) == (1500, 0.003)
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
    assert out.claims == [Claim(text="a person opens a door", verdict="supported", at=2.5)]


# -- (b) an unsupported claim is cut, then the rest is re-verified -----------------------


def test_not_visible_claim_is_removed_and_reverified(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(
                ("a person", "supported"),
                ("in a red coat", "not_visible"),
                ("opens a door", "supported"),
            ),  # fmt: skip
            _supported("a person", "opens a door"),
        ],
        remove=[{"text": "A person opens a door."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "remove", "verify"]
    assert out.status == "verified"
    assert out.text == "A person opens a door."
    assert "1 claim removed: in a red coat" in out.note
    rm = tf.of("remove")[0]
    assert (rm["model"], rm["stage"], rm["thinking"]) == (LIGHTNING, "finalize", False)
    assert rm["schema"] == TEXT_SCHEMA
    assert "in a red coat" in _user_text(rm) and _description(rm) == TEXT
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
        remove=[{"text": "A person opens a door."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]))
    assert out.status == "verified" and "red" not in out.text


def _new_failure_each_round(*last_round, then=()):
    """A verifier that rejects a different detail of the line in each of rounds 1-3 ("red",
    "coat", "opens": every line these tests verify says them), then `last_round`, then the
    answers in `then`."""
    answers = iter(
        [_claims(("a person", "supported"), (word, "not_visible"))
         for word in ("red", "coat", "opens")]
        + [_claims(*last_round), *then]
    )  # fmt: skip
    return lambda call: next(answers)


def _changed_each_time():
    """A remover whose rewrite always differs from its input."""
    return lambda call: {"text": _description(call).rstrip(".") + " again."}


def test_contradicted_claim_never_kept_even_when_held(tmp_path, video):
    """On the last round the cue is held for a person, but a contradicted claim is still cut
    from its text; a claim that is only not visible stays for the reviewer to judge."""
    removes = iter(["A person in a red coat opens the door.", "A person in a red coat opens it.",
                    TEXT, "A person opens a door."])  # fmt: skip
    tf = FakeTF(
        verify=_new_failure_each_round(
            ("a red coat", "contradicted"),
            ("a door", "not_visible"),
            then=[_claims(("a person", "supported"), ("opens a door", "not_visible"))],
        ),  # fmt: skip
        remove=lambda call: {"text": next(removes)},
    )
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held"
    assert len(tf.of("verify")) == 5 and len(tf.of("remove")) == 4
    last = _user_text(tf.of("remove")[-1]).split("<remove>", 1)[1]
    assert "a red coat" in last and "a door" not in last
    assert out.text == "A person opens a door."
    # the claims it is held with are those of the cut text, judged on it (a fifth check)
    assert _description(tf.of("verify")[-1]) == out.text == out.checks[-1].text
    assert [(c.text, c.verdict) for c in out.claims] == [
        ("a person", "supported"), ("opens a door", "not_visible")
    ]  # fmt: skip
    assert "not verified after 4 rounds" in out.note and "a red coat" in out.note
    assert speech.calls == []
    assert (out.audio_path, out.audio_dur) == (None, None)


def test_claim_rejected_again_after_removal_is_held(tmp_path, video):
    tf = FakeTF(
        verify=lambda call: _claims(("a person", "supported"), ("a red coat", "not_visible")),
        remove=[{"text": "A person in a coat opens a door."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]))
    assert tf.roles() == ["verify", "remove", "verify"]
    assert out.status == "held" and out.text == "A person in a coat opens a door."
    assert "rejected a removed claim again: a red coat" in out.note


def test_removal_that_changes_nothing_is_held(tmp_path, video):
    tf = FakeTF(
        verify=[_claims(("a person", "supported"), ("a red coat", "not_visible"))],
        remove=lambda call: {"text": _description(call)},
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]))
    assert tf.roles() == ["verify", "remove"]
    assert out.status == "held" and out.text == TEXT
    assert "removed nothing" in out.note


def test_contradiction_that_cannot_be_cut_withholds_the_text(tmp_path, video):
    tf = FakeTF(
        verify=[_claims(("a person", "supported"), ("a red coat", "contradicted"))],
        remove=lambda call: {"text": _description(call)},
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]))
    assert tf.roles() == ["verify", "remove"]
    assert out.status == "held" and out.text == ""
    assert "withheld" in out.note
    assert out.claims == []  # nothing is said, so no claim is published; the check is kept
    assert [k.verdict for k in out.checks[-1].claims] == ["supported", "contradicted"]


def test_everything_removed_is_held(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(("a person in a red coat", "not_visible"), ("opens a door", "not_visible"))
        ],
        remove=[{"text": "  "}],
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
        remove=[{"text": "A person opens a door."}],
    )
    cue = _cue('A sign reads: "EXIT". A person opens a door.')
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=cue, ctx=ctx)
    assert tf.roles() == ["verify", "remove", "verify"]
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
    tf = FakeTF(
        verify=lambda call: _supported("a person opens a door"),
        compress=lambda call: {"text": _description(call)},  # no shorter
    )
    speech = FakeSpeech(lambda text: 8.0)
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held"
    assert len(speech.calls) == 4 and len(tf.of("compress")) == 3
    assert "does not fit" in out.note
    assert (out.audio_path, out.audio_dur, out.end) == (None, None, None)


# -- (d2) a compression that cuts too much is tried once more, then held ---------------------

LOGO = EV.model_copy(update={"id": "e002", "t_start": 3.0, "t_end": 6.0, "text": "A logo."})
# live csc_intro run: Lightning answered this 14-word line (target 10) with "#CLIMATECHAMPIONS."
HASHTAG = (
    'A hashtag reads: "#CLIMATECHAMPIONS". The USGS logo and tagline appear on a black background.'
)


def _two_visuals(tmp_path, video, tf, speech):
    ctx = CTX.model_copy(update={"verbatim": ["#CLIMATECHAMPIONS"]})
    cue = _cue(HASHTAG, event_ids=["e001", "e002"])
    events = {"e001": EV, "e002": LOGO}
    return _run(tmp_path, video, tf, speech, cue=cue, events=events, ctx=ctx)


def test_a_compression_that_guts_the_line_is_retried_with_a_note(tmp_path, video):
    shorter = 'A hashtag reads: "#CLIMATECHAMPIONS". The USGS logo appears.'  # 8 words
    tf = FakeTF(
        verify=[_supported("a hashtag reads #CLIMATECHAMPIONS", "the USGS logo")] * 2,
        compress=[{"text": "#CLIMATECHAMPIONS."}, {"text": shorter}],
    )
    out = _two_visuals(tmp_path, video, tf, FakeSpeech([5.0, 3.5]))  # usable 4.0 s
    assert tf.roles() == ["verify", "compress", "compress", "verify"]
    first, retry = (_user_text(c) for c in tf.of("compress"))
    assert "about 10 words" in first and "previous answer" not in first
    assert 'previous answer "#CLIMATECHAMPIONS." kept 1 of about 10 words' in retry
    assert out.status == "verified" and out.text == shorter
    assert "compressed 14→8 words" in out.note


def test_a_compression_that_drops_a_visual_twice_is_held_with_the_full_line(tmp_path, video):
    one = 'A black screen shows the hashtag "#CLIMATECHAMPIONS".'  # 7 words, 1 sentence
    tf = FakeTF(
        verify=[_supported("a hashtag reads #CLIMATECHAMPIONS", "the USGS logo")],
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
    ("the person wears a red coat", "not_visible"),
    ("a cat runs past", "supported"),
)


def _coat_cat(tmp_path, video, tf, speech):
    cue = _cue(COAT_CAT, event_ids=["e001", "e002"])
    return _run(tmp_path, video, tf, speech, cue=cue, events={"e001": EV, "e002": TABBY})


def test_a_claim_cut_that_drops_another_visual_is_retried_with_a_note(tmp_path, video):
    tf = FakeTF(
        verify=[COAT_CLAIMS, _supported("a person opens a door", "a cat runs past")],
        remove=[
            {"text": "A person opens a door."},
            {"text": "A person opens a door. A cat runs past."},
        ],
    )
    out = _coat_cat(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "remove", "remove", "verify"]
    first, retry = (_user_text(c) for c in tf.of("remove"))
    assert "previous answer" not in first
    assert 'Your previous answer "A person opens a door." dropped "A cat runs past."' in retry
    assert out.status == "verified" and out.text == "A person opens a door. A cat runs past."
    assert "1 claim removed: the person wears a red coat" in out.note


def test_a_claim_cut_that_drops_another_visual_twice_is_held_with_the_line(tmp_path, video):
    tf = FakeTF(verify=[COAT_CLAIMS], remove=[{"text": "A person opens a door."}] * 2)
    out = _coat_cat(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "remove", "remove"]
    assert out.status == "held" and out.text == COAT_CAT  # nothing contradicted: kept for review
    assert 'the claim cut dropped "A cat runs past." twice' in out.note
    assert [c.verdict for c in out.claims] == ["supported", "not_visible", "supported"]


def test_a_claim_cut_may_drop_the_sentence_its_claims_were_about(tmp_path, video):
    tf = FakeTF(
        verify=[
            _claims(("a person opens a door", "supported"), ("a cat runs past", "not_visible")),
            _supported("a person opens a door"),
        ],
        remove=[{"text": "A person in a red coat opens a door."}],
    )
    out = _coat_cat(tmp_path, video, tf, FakeSpeech([2.0]))
    assert tf.roles() == ["verify", "remove", "verify"]
    assert out.status == "verified" and out.text == "A person in a red coat opens a door."


def test_remove_claims_sends_the_note_only_when_given():
    bad = [Claim(text="red", verdict="not_visible", at=None)]
    tf = FakeTF(remove=[{"text": "A door opens."}] * 2)
    remove_claims("A red door opens.", bad, tf, run_id="r1")
    remove_claims("A red door opens.", bad, tf, run_id="r1", note="it dropped a visual")
    plain, noted = (_user_text(c) for c in tf.calls)
    assert "it dropped a visual" not in plain and "it dropped a visual" in noted


# -- (e) verification never passes ----------------------------------------------------------


def test_verify_always_fails_is_held_after_four_rounds(tmp_path, video):
    """A new claim fails in every round; each is cut, and the fourth failure holds the cue."""
    tf = FakeTF(
        verify=_new_failure_each_round(("a person", "supported"), ("door", "not_visible")),
        remove=_changed_each_time(),
    )
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held"
    assert len(tf.of("verify")) == 4 and len(tf.of("remove")) == 3
    assert "not verified after 4 rounds: door (not visible)" in out.note
    assert "3 claims removed: red; coat; opens" in out.note
    assert speech.calls == []
    assert out.start == GAP.usable_start
    assert {c["stage"] for c in tf.calls} == {"finalize"}


def test_no_claims_from_the_verifier_is_held(tmp_path, video):
    tf = FakeTF(verify=[{"claims": []}])
    speech = FakeSpeech([])
    out = _run(tmp_path, video, tf, speech)
    assert out.status == "held" and speech.calls == []


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
        remove=[{"text": "A person waves."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([2.0]), cue=_ahead_cue(), events=AHEAD_EVENTS)
    assert out.status == "verified" and out.text == "A person waves."
    assert out.ahead is False and "look-ahead sentence removed" in out.note


def test_removal_that_strips_the_prefix_but_keeps_the_sentence_is_held(tmp_path, video):
    tf = FakeTF(
        verify=[_claims(("a person waves", "supported"), ("the car is red", "not_visible"))],
        remove=[{"text": "A person waves. A car drives past."}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([]), cue=_ahead_cue(), events=AHEAD_EVENTS)
    assert tf.roles() == ["verify", "remove"]
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
    assert out.status == "held" and len(speech.calls) == 4
    assert "sentence 2 would start at 4.00 s, after its visual ends at 3.00 s" in out.note


def test_fit_is_measured_from_the_placed_start(tmp_path, video):
    tf = FakeTF(verify=[_supported("a person opens a door")])
    out = _run(tmp_path, video, tf, FakeSpeech([3.5]), cue=_cue(start=3.0))
    assert out.status == "verified" and out.start == 2.0 and out.tempo == 1.0
    assert tf.roles() == ["verify"]


def test_quote_lost_with_an_unrelated_claim_is_demanded_back(tmp_path, video):
    ctx = CTX.model_copy(update={"verbatim": ["EXIT"]})
    tf = FakeTF(
        verify=[
            _claims(('a sign reads "EXIT"', "supported"), ("in a red coat", "not_visible")),
            _supported('a sign reads "EXIT"', "a person opens a door"),
        ],
        # the sign sentence survives but loses its quote (a whole lost sentence is asked back
        # from the cut itself: see the (d3) tests)
        remove=[{"text": "A sign above the door. A person opens a door."}],
        repair=[{"text": 'A sign above the door reads: "EXIT". A person opens a door.'}],
    )
    cue = _cue('A sign above the door reads: "EXIT". A person in a red coat opens a door.')
    out = _run(tmp_path, video, tf, FakeSpeech([2.5]), cue=cue, ctx=ctx)
    assert tf.roles() == ["verify", "remove", "repair", "verify"]
    assert "R19-verbatim" in _user_text(tf.of("repair")[0])
    assert out.status == "verified" and '"EXIT"' in out.text


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
    assert cuts == [(2.0, 14.0, 360)]


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


def test_clip_window_capped_at_12_s_around_the_events():
    assert clip_window([_e(0.0, 20.0)], 30.0) == (4.0, 16.0)
    assert clip_window([_e(10.0, 14.0, "a"), _e(30.0, 40.0, "b")], 60.0) == (19.0, 31.0)
    assert clip_window([_e(0.0, 20.0)], 14.0) == (2.0, 14.0)  # shifted inside the video
    assert clip_window([_e(0.0, 2.0)], 1.0) == (0.0, 1.0)


# -- model calls on their own -------------------------------------------------------------------


def test_verify_parses_and_offsets(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\0" * 16)
    tf = FakeTF(
        verify=[
            {
                "claims": [
                    {"text": "a door", "verdict": "supported", "at": 1.0},
                    {"text": "a cat", "verdict": "not_visible", "at": None},
                    {"text": "  ", "verdict": "supported", "at": 0.0},
                ]
            }
        ]
    )
    out = verify("A door. A cat.", clip, tf, run_id="r1", names={"Emo"}, offset=10.0)
    assert out == [
        Claim(text="a door", verdict="supported", at=11.0),
        Claim(text="a cat", verdict="not_visible", at=None),
    ]
    assert "Emo" in _user_text(tf.calls[0]).split("<names>", 1)[1]


def test_verify_rejects_an_answer_without_claims(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\0" * 16)
    with pytest.raises(ValueError):
        verify("A door.", clip, FakeTF(verify=[{"items": []}]), run_id="r1")


def test_verify_keeps_failed_claims_without_text(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\0" * 16)
    answer = {
        "claims": [
            {"text": "", "verdict": "not_visible", "at": None},
            {"text": " ", "verdict": "contradicted", "at": 2.0},
            {"text": "", "verdict": "supported", "at": 1.0},
            {"text": "a door", "verdict": "supported", "at": 1.0},
        ]
    }
    out = verify("A door.", clip, FakeTF(verify=[answer]), run_id="r1")
    assert [(c.text, c.verdict) for c in out] == [
        (UNSPECIFIED, "not_visible"), (UNSPECIFIED, "contradicted"), ("a door", "supported"),
    ]  # fmt: skip
    text = VERIFY_SCHEMA["properties"]["claims"]["items"]["properties"]["text"]
    assert text == {"type": "string", "minLength": 1}


def test_remove_claims_lists_the_bad_claims():
    tf = FakeTF(remove=[{"text": "A door opens."}])
    bad = [Claim(text="a red door", verdict="contradicted", at=1.0)]
    assert remove_claims("A red door opens.", bad, tf, run_id="r1") == "A door opens."
    assert "a red door" in _user_text(tf.calls[0])
    assert tf.calls[0]["role"] == "remove"
    assert remove_claims("A door opens.", [], tf, run_id="r1") == "A door opens."
    assert len(tf.calls) == 1


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


def test_a_quote_inside_a_longer_word_of_a_cut_claim_is_still_demanded(tmp_path, video):
    """Cutting "a person exits the building" does not cut the sign "EXIT": only a quote the
    cut claims say as a whole word stops being demanded (R19)."""
    ctx = CTX.model_copy(update={"verbatim": ["EXIT"]})
    tf = FakeTF(
        verify=[
            _claims(
                ('a sign reads "EXIT"', "supported"), ("a person exits the building", "not_visible")
            ),
            _supported('a sign reads "EXIT"'),
        ],  # fmt: skip
        remove=[{"text": "A sign above the door."}],
        repair=[{"text": 'A sign above the door reads: "EXIT".'}],
    )
    cue = _cue('A sign above the door reads: "EXIT". A person exits the building.')
    out = _run(tmp_path, video, tf, FakeSpeech([2.5]), cue=cue, ctx=ctx)
    assert tf.roles() == ["verify", "remove", "repair", "verify"]
    assert out.status == "verified" and '"EXIT"' in out.text


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
        remove=[{"text": "A person opens a door."}],
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
    assert clip_window([_e(0.0, 20.0)], 30.0) == (4.0, 16.0)  # the cap still holds


def test_repeated_claims_are_judged_once():
    """Identical claims (case and spacing aside) count once; a repeat with a worse verdict
    makes the kept one worse, never better."""
    same = [{"text": "Winged figures fly past.", "verdict": "supported", "at": 1.0}] * 24
    assert len(parse_claims({"claims": same})) == 1
    mixed = {"claims": [
        {"text": "A cat sits", "verdict": "supported", "at": 1.0},
        {"text": "a  CAT sits", "verdict": "not_visible", "at": None},
        {"text": "A dog barks", "verdict": "supported", "at": 2.0},
    ]}  # fmt: skip
    got = parse_claims(mixed)
    assert [(c.text, c.verdict) for c in got] == [
        ("A cat sits", "not_visible"), ("A dog barks", "supported")
    ]  # fmt: skip


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
    """A line compressed after it was verified is verified again, and the claims it carries
    are the final text's, each judged on that text. What the verifier lists beyond the line
    (other things the frames show) is not a claim of the line and is never published."""
    long_text = "A person in a long red coat opens the wooden door of the shed."  # 14 words
    final = "A person in a red coat opens a door."
    tf = FakeTF(
        verify=[
            _supported("a person", "a long red coat", "opens the wooden door of the shed"),
            _supported(
                "a person",
                "wears a red coat",
                "opens a door",
                "the shed has a green roof",
                "a dog sleeps on the porch",
            ),
        ],  # fmt: skip
        compress=[{"text": final}],
    )
    out = _run(tmp_path, video, tf, FakeSpeech([5.6, 3.0]), cue=_cue(long_text))
    assert out.status == "verified" and out.text == final
    assert [c.text for c in out.claims] == ["a person", "wears a red coat", "opens a door"]
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
