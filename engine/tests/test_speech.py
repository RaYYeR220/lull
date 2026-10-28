import json

import httpx
import pytest

from lull.speech import NemoSpeechHTTP, SpeechError, assign_speakers
from lull.types import Turn, Word

ASR = {
    "duration": 5,
    "language": "",
    "task": "transcribe",
    "text": "Safe here. Let's go.",
    "words": [
        {"word": " go.", "start": 1.5, "end": 1.9, "confidence": 1},
        {"word": "Safe", "start": 0.24, "end": 0.64, "confidence": 1},
        {"word": "  ", "start": 0.7, "end": 0.8, "confidence": 1},
        {"word": "here.", "start": -0.1, "end": 1.0, "confidence": 1},
    ],
}
DIAR = {
    "segments": [
        {"start": 0, "end": 1.479, "speaker": 1},
        {"start": 1.5, "end": 3, "speaker": 0},
    ]
}


def make(handler, api_key=None):
    return NemoSpeechHTTP("http://s", api_key, transport=httpx.MockTransport(handler))


def _wav(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    return wav


def test_transcribe_parses_and_cleans(tmp_path):
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = req.content
        return httpx.Response(200, json=ASR)

    words = make(h).transcribe(_wav(tmp_path))
    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["auth"] is None
    assert b"verbose_json" in seen["body"] and b"timestamp_granularities[]" in seen["body"]
    assert [w.text for w in words] == ["here.", "Safe", "go."]
    assert words[0].start == 0.0
    assert all(w.start >= 0 for w in words)
    assert [w.start for w in words] == sorted(w.start for w in words)


def test_auth_header_and_diarize(tmp_path):
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=DIAR)

    turns = make(h, "K").diarize(_wav(tmp_path))
    assert seen["auth"] == "Bearer K"
    assert turns == [Turn(speaker=1, start=0, end=1.479), Turn(speaker=0, start=1.5, end=3)]


def test_synthesize_writes_wav(tmp_path):
    seen = {}

    def h(req):
        seen["json"] = json.loads(req.content)
        return httpx.Response(200, content=b"RIFFxxxx", headers={"content-type": "audio/wav"})

    dst = tmp_path / "sub" / "o.wav"
    assert make(h).synthesize("hi", "Sofia", dst) == dst
    assert dst.read_bytes() == b"RIFFxxxx"
    assert seen["json"] == {
        "model": "magpie",
        "input": "hi",
        "voice": "Sofia",
        "response_format": "wav",
    }


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(200, content=b"", headers={"content-type": "audio/wav"}),
        httpx.Response(200, json={"a": 1}),
        httpx.Response(500, text="boom"),
        httpx.Response(401, text="nope"),
    ],
)
def test_synthesize_errors(tmp_path, resp):
    with pytest.raises(SpeechError):
        make(lambda r: resp).synthesize("hi", "Sofia", tmp_path / "o.wav")


def test_http_error_has_status_and_body(tmp_path):
    with pytest.raises(SpeechError, match="503.*overloaded"):
        make(lambda r: httpx.Response(503, text="overloaded")).transcribe(_wav(tmp_path))


def test_health():
    assert make(lambda r: httpx.Response(200, json={"status": "ok", "version": "0.2.0"})).health()
    assert not make(lambda r: httpx.Response(200, json={"status": "bad"})).health()
    assert not make(lambda r: httpx.Response(500)).health()


def test_assign_speakers_exact():
    turns = [Turn(speaker=0, start=0, end=2), Turn(speaker=1, start=2, end=4)]
    words = [
        Word(text="a", start=0.1, end=0.5),
        Word(text="b", start=1.5, end=2.2),  # 0.5 vs 0.2 -> 0
        Word(text="c", start=1.9, end=2.9),  # 0.1 vs 0.9 -> 1
        Word(text="d", start=3.0, end=3.5),
    ]
    out = assign_speakers(words, turns)
    assert [w.speaker for w in out] == [0, 0, 1, 1]
    assert all(w.speaker is None for w in words)


def test_assign_speakers_tie_and_nearest():
    turns = [Turn(speaker=5, start=0, end=1), Turn(speaker=6, start=1, end=2)]
    tie = assign_speakers([Word(text="x", start=0.5, end=1.5)], turns)
    assert tie[0].speaker == 5
    near = assign_speakers(
        [Word(text="n", start=2.3, end=2.4), Word(text="far", start=3.0, end=3.1)], turns
    )
    assert [w.speaker for w in near] == [6, None]
    assert assign_speakers([Word(text="x", start=0, end=1)], [])[0].speaker is None


def test_the_speech_server_has_bounded_waits_and_a_late_answer_is_a_timeout(tmp_path):
    from lull.speech import SpeechTimeout

    def slow(request):
        raise httpx.ReadTimeout("the server did not answer", request=request)

    client = NemoSpeechHTTP("http://speech.test", transport=httpx.MockTransport(slow))
    t = client._http.timeout
    assert t.connect <= 10 and t.read <= 300 and t.write <= 300 and t.pool <= 10
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    out = tmp_path / "o.wav"
    for call in (lambda: client.transcribe(wav), lambda: client.synthesize("A.", "Sofia", out)):
        with pytest.raises(SpeechTimeout) as err:
            call()
        assert isinstance(err.value, TimeoutError) and isinstance(err.value, SpeechError)
