import os

import pytest

from lull.speech import NemoSpeechHTTP

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LULL_LIVE") != "1", reason="set LULL_LIVE=1 to run"),
]


@pytest.fixture
def client():
    return NemoSpeechHTTP(
        os.environ.get("LULL_SPEECH_URL") or "http://127.0.0.1:8089",
        os.environ.get("LULL_SPEECH_KEY") or None,
    )


def test_roundtrip(client, tmp_path):
    assert client.health()
    text = "The quick brown fox jumps over the lazy dog."
    wav = client.synthesize(text, "Sofia", tmp_path / "t.wav")
    words = client.transcribe(wav)
    assert len(words) >= 3
    starts = [w.start for w in words]
    assert starts == sorted(starts) and starts[-1] > starts[0]
    assert len(client.diarize(wav)) >= 1
