from pathlib import Path

import pytest

from lull import media


def test_probe_tone_slides(tone_slides: Path):
    p = media.probe(tone_slides)
    assert p.duration == pytest.approx(12.0, abs=0.05)
    assert p.has_audio is True
    assert (p.width, p.height) == (640, 360)
    assert p.fps == pytest.approx(30.0)


def test_probe_noaudio_portrait(noaudio: Path):
    p = media.probe(noaudio)
    assert p.has_audio is False
    assert (p.width, p.height) == (720, 1280)


def test_probe_missing_file_raises(tmp_path: Path):
    with pytest.raises(media.MediaError):
        media.probe(tmp_path / "nope.mp4")


def test_extract_wav_and_audio_duration(tone_slides: Path, tmp_path: Path):
    wav = media.extract_wav(tone_slides, tmp_path / "a.wav")
    assert wav.exists()
    assert media.audio_duration(wav) == pytest.approx(12.0, abs=0.1)


def test_scene_cuts(tone_slides: Path):
    cuts = media.scene_cuts(tone_slides)
    assert len(cuts) == 1
    assert cuts[0] == pytest.approx(6.0, abs=0.2)


def test_make_proxy_720(tone_slides: Path, tmp_path: Path):
    big = media.probe(tone_slides)
    out = media.make_proxy(tone_slides, tmp_path / "p.mp4", height=360)
    assert out.stat().st_size < 10 * 2**20
    p = media.probe(out)
    assert p.height == 360 and p.has_audio is False
    assert big.width == p.width


def test_make_proxy_forced_smaller(noaudio: Path, tmp_path: Path):
    out = media.make_proxy(noaudio, tmp_path / "p.mp4", max_bytes=200_000, height=720)
    assert out.stat().st_size < 200_000
    assert media.probe(out).height <= 720


def test_make_proxy_portrait_even_width(noaudio: Path, tmp_path: Path):
    out = media.make_proxy(noaudio, tmp_path / "p.mp4", height=720)
    p = media.probe(out)
    assert p.height == 720 and p.width % 2 == 0


def test_make_proxy_too_big_raises(noaudio: Path, tmp_path: Path):
    with pytest.raises(media.MediaError):
        media.make_proxy(noaudio, tmp_path / "p.mp4", max_bytes=100)


def test_cut_clip(tone_slides: Path, tmp_path: Path):
    out = media.cut_clip(tone_slides, tmp_path / "c.mp4", 2.0, 5.0, height=180)
    p = media.probe(out)
    assert p.duration == pytest.approx(3.0, abs=0.2)
    assert p.height == 180


def test_frame_at(tone_slides: Path, tmp_path: Path):
    out = media.frame_at(tone_slides, 3.0, tmp_path / "f.jpg")
    assert out.exists() and out.read_bytes()[:2] == b"\xff\xd8"


def test_loudness_profile(tone_slides: Path, tmp_path: Path):
    wav = media.extract_wav(tone_slides, tmp_path / "a.wav")
    prof = media.loudness_profile(wav)
    assert prof[0][0] == 0.0
    assert any(db > -30 for t, db in prof if t < 4.0)
    assert all(db < -60 for t, db in prof if t >= 5.0)
