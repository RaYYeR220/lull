import subprocess
from pathlib import Path

import pytest


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


@pytest.fixture(scope="session")
def tone_slides(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s, 640x360 30 fps; red 0-6 s, blue 6-12 s; 440 Hz sine 0-4 s then silence."""
    out = tmp_path_factory.mktemp("media") / "tone_slides.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=6",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4,apad=whole_dur=12",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "12", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def noaudio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """4 s portrait 720x1280 with no audio stream."""
    out = tmp_path_factory.mktemp("media") / "noaudio.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25:d=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def loud_burst(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """6 s, quiet 440 Hz hum (about -32 dBFS) with a 24 dB louder burst at 2.0-3.0 s."""
    out = tmp_path_factory.mktemp("media") / "loud_burst.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-af", "volume=0.04,volume=16:enable='between(t,2,3)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "6", str(out),
    )  # fmt: skip
    return out


@pytest.fixture
def fast_media(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A pipeline run without most ffmpeg launches (each costs about a second here): watch and
    verify clips, scene cuts, audio measurement, the mix, the extended video and keyframes are
    stand-ins. Probing and listening still read the real video. Returns what the mix saw."""
    import wave

    from lull import media
    from lull.stages import finalize as fin
    from lull.stages import mix as mixer
    from lull.stages import voice as voice_mod
    from lull.stages import watch as watch_mod

    seen: dict[str, list] = {"mix": [], "extended": []}

    def clip(src, dst, start, end, *, height=480):
        Path(dst).write_bytes(b"\0" * 16)
        return Path(dst)

    def seconds(path):
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()

    def fake_mix(video, cues, out_dir, **kw):
        seen["mix"].append([c.id for c in cues])
        audio, vid = out_dir / mixer.AD_AUDIO, out_dir / mixer.AD_VIDEO
        audio.write_bytes(b"m4a")
        vid.write_bytes(b"mp4")
        return mixer.MixResult(ad_audio=audio, ad_video=vid)

    def fake_extended(video, cues, out_dir, **kw):
        seen["extended"].append([c.id for c in cues])
        return None

    def fake_frames(src, shots, **kw):
        for _, d in shots:
            Path(d).write_bytes(b"\xff\xd8jpg")
        return [Path(d) for _, d in shots]

    def fake_levels(video, info, cues):
        return mixer.Levels(programme=-23.0, cues={str(c.audio_path): -20.0 for c in cues})

    monkeypatch.setattr(watch_mod, "cut_clip", clip)
    monkeypatch.setattr(watch_mod, "scene_cuts", lambda src, threshold=0.3: [])
    monkeypatch.setattr(fin, "cut_clip", clip)
    monkeypatch.setattr(fin, "audio_duration", seconds)
    monkeypatch.setattr(voice_mod, "audio_duration", seconds)
    monkeypatch.setattr(mixer, "measure_levels", fake_levels)
    monkeypatch.setattr(mixer, "mix", fake_mix)
    monkeypatch.setattr(mixer, "extended_video", fake_extended)
    monkeypatch.setattr(media, "frames_at", fake_frames)
    return seen
