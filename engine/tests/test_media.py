import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from locks import assert_locked
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


def test_audio_duration_reads_only_wav(tone_slides: Path, tmp_path: Path):
    """It measures the voice's WAV files; anything else (here an MP4 named .wav) is refused
    rather than demuxed."""
    fake = tmp_path / "voice.wav"
    fake.write_bytes(tone_slides.read_bytes())
    with pytest.raises(media.MediaError):
        media.audio_duration(fake)


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


# -- inputs are local files in a known container -----------------------------------------


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every command lull.media launches, still run for real."""
    seen: list[list[str]] = []
    real = media._exec

    def spy(cmd, *args, **kwargs):
        seen.append([str(c) for c in cmd])
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(media, "_exec", spy)
    return seen


def test_every_media_input_is_locked_to_local_files(tone_slides, tmp_path, commands):
    wav = media.extract_wav(tone_slides, tmp_path / "a.wav")
    media.audio_duration(wav)
    media.probe(tone_slides)
    media.make_proxy(tone_slides, tmp_path / "p.mp4", height=180)
    media.cut_clip(tone_slides, tmp_path / "c.mp4", 1.0, 2.0, height=180)
    media.atempo(wav, tmp_path / "t.wav", 1.05)
    media.frame_at(tone_slides, 1.0, tmp_path / "f.jpg")
    media.frames_at(tone_slides, [(1.0, tmp_path / "g.jpg"), (2.0, tmp_path / "h.jpg")])
    media.scene_cuts(tone_slides)
    # 9 runs, plus the duration probes their time limits need (extract_wav, make_proxy,
    # atempo, scene_cuts)
    assert len(commands) == 13
    for cmd in commands:
        assert_locked(cmd)


def test_probe_reports_the_container(tone_slides, tmp_path):
    p = media.probe(tone_slides)
    assert p.format_name == "mov,mp4,m4a,3gp,3g2,mj2"
    media.assert_safe_container(p)
    mkv = tmp_path / "copy.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tone_slides), "-c", "copy",
                    str(mkv)], check=True, capture_output=True)  # fmt: skip
    q = media.probe(mkv)
    assert q.format_name == "matroska,webm"
    media.assert_safe_container(q)


@pytest.mark.parametrize(
    "name", ["mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "mp4", "webm", "mov", "3gp"]
)
def test_known_containers_are_accepted(name):
    media.assert_safe_container(media.Probe(1.0, 2, 2, 25.0, False, "h264", format_name=name))


@pytest.mark.parametrize(
    "name",
    ["hls", "concat", "image2", "lavfi", "wav", "mpegts", "", "tee", "hls,mp4", "concat,mov"],
)
def test_other_containers_are_refused(name):
    info = media.Probe(1.0, 2, 2, 25.0, False, "h264", format_name=name)
    with pytest.raises(media.MediaError, match="container"):
        media.assert_safe_container(info)


def _playlist(url: str) -> str:
    return f"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\n{url}\n#EXT-X-ENDLIST\n"


def _rejected(path: Path) -> None:
    """probe + assert_safe_container refuse `path` (whichever of the two notices first)."""
    with pytest.raises(media.MediaError):
        media.assert_safe_container(media.probe(path))


def test_a_concat_list_named_mp4_is_refused(tone_slides, tmp_path, commands):
    shutil.copy(tone_slides, tmp_path / "real.mp4")
    evil = tmp_path / "evil.mp4"
    evil.write_text("ffconcat version 1.0\nfile real.mp4\nduration 12.0\n", encoding="utf-8")
    assert "concat" in subprocess.run(  # without the lock ffprobe would read it as a video
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "csv=p=0",
         str(evil)], capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    commands.clear()
    _rejected(evil)
    assert commands and all("-protocol_whitelist" in c for c in commands)


def test_an_hls_playlist_never_reaches_the_network(tmp_path, commands):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    hits: list[int] = []
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            hits.append(1)
            conn.close()

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    try:
        for name, url in [
            ("evil.mp4", "http://169.254.169.254/x"),
            ("evil.m3u8", f"http://127.0.0.1:{port}/x.ts"),
            ("upload", f"http://127.0.0.1:{port}/y.ts"),
        ]:
            path = tmp_path / name
            path.write_text(_playlist(url), encoding="utf-8")
            _rejected(path)
    finally:
        stop.set()
        t.join(timeout=2)
        srv.close()
    assert hits == []
    for cmd in commands:
        assert_locked(cmd)


# -- edge cases ------------------------------------------------------------------


def test_probe_reports_the_displayed_size_of_a_rotated_video(tmp_path, noaudio):
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-display_rotation", "90",
                    "-i", str(noaudio), "-c", "copy", str(rotated)],
                   check=True, capture_output=True)  # fmt: skip
    p = media.probe(rotated)
    assert (p.width, p.height) == (1280, 720)  # stored 720x1280, shown turned a quarter


def test_make_proxy_never_goes_above_the_requested_height_and_cleans_up(tmp_path, monkeypatch):
    heights: list[str] = []

    def fake(*args, **kw):
        heights.append(next(a for a in args if str(a).startswith("scale=")))
        Path(args[-1]).write_bytes(b"\0" * 2000)  # always too big
        return None

    monkeypatch.setattr(media, "_ffmpeg", fake)
    dst = tmp_path / "p.mp4"
    with pytest.raises(media.MediaError, match="2000 bytes"):
        media.make_proxy(tmp_path / "in.mp4", dst, max_bytes=1000, height=360)
    assert heights == ["scale=-2:360", "scale=-2:360", "scale=-2:360"]  # no 480 rung
    assert not dst.exists()  # the oversize attempt is not left behind


def test_frame_at_never_leaves_a_stale_frame(tone_slides, tmp_path):
    dst = tmp_path / "f.jpg"
    dst.write_bytes(b"an old frame")
    with pytest.raises(media.MediaError):
        media.frame_at(tone_slides, 30.0, dst)  # past the end: no frame
    assert not dst.exists()


def test_extract_wav_from_a_video_without_sound_is_an_error(noaudio, tmp_path):
    with pytest.raises(media.MediaError):
        media.extract_wav(noaudio, tmp_path / "a.wav")


def test_paths_with_spaces_work(tone_slides, tmp_path):
    folder = tmp_path / "a folder with spaces"
    folder.mkdir()
    video = folder / "my video (1).mp4"
    shutil.copy(tone_slides, video)
    assert media.probe(video).duration == pytest.approx(12.0, abs=0.05)
    assert media.audio_duration(media.extract_wav(video, folder / "sound track.wav")) > 11.9
    assert media.probe(media.cut_clip(video, folder / "a clip.mp4", 1.0, 2.0)).height == 480
    assert media.frame_at(video, 1.0, folder / "a frame.jpg").exists()


def test_a_variable_frame_rate_video_probes_and_cuts(tmp_path):
    vfr = tmp_path / "vfr.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=s=160x120:r=30:d=2",
                    "-vf", "setpts='if(lt(T,1),PTS,1/TB+(PTS-1/TB)*3)'", "-fps_mode", "vfr",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vfr)],
                   check=True, capture_output=True)  # fmt: skip
    p = media.probe(vfr)
    assert p.duration == pytest.approx(3.8, abs=0.1) and 1.0 <= p.fps <= 240.0
    frames = media.frames_at(vfr, [(0.5, tmp_path / "a.jpg"), (3.0, tmp_path / "b.jpg")])
    assert all(f.read_bytes()[:2] == b"\xff\xd8" for f in frames)
    clip = media.cut_clip(vfr, tmp_path / "c.mp4", 1.0, 3.0, height=120)
    assert media.probe(clip).duration == pytest.approx(2.0, abs=0.2)


# -- lists and playlists --------------------------------------------------------------------------


def test_probe_refuses_a_list_before_opening_what_it_names(tone_slides, tmp_path, commands):
    """ffprobe gets a format whitelist, so a concat list is refused before it is read: a
    list naming an existing file and one naming a missing file fail the same way (no oracle
    for which files exist) and the named file is never opened."""
    shutil.copy(tone_slides, tmp_path / "real.mp4")
    errors = []
    for name, target in (("a.mp4", "real.mp4"), ("b.mp4", "nothere.mp4")):
        path = tmp_path / name
        path.write_text(f"ffconcat version 1.0\nfile {target}\nduration 12.0\n", encoding="utf-8")
        with pytest.raises(media.MediaError) as err:
            media.probe(path)
        errors.append(str(err.value))
    assert errors[0] == errors[1]
    assert "container" in errors[0] and "real.mp4" not in errors[0]
    [cmd] = [c for c in commands if c[-1].endswith("a.mp4")]
    assert "-format_whitelist" in cmd
    debug = [("debug" if a == "error" else a) for a in cmd]  # the same call, verbose
    log = subprocess.run(debug, capture_output=True, text=True).stderr
    assert "a.mp4" in log and "real.mp4" not in log  # opened the list, never what it names


# -- time limits ---------------------------------------------------------------------------------


def test_a_tool_that_runs_past_its_limit_is_stopped_and_raises_a_timeout(tmp_path):
    """A command that hangs is killed at its limit (no process left behind) and raises a
    TimeoutError the pipeline reports like any failed stage."""
    import sys
    import time

    from lull.stages.publish import _alive

    pid_file = tmp_path / "pid"
    code = f"import os, time; open(r'{pid_file}', 'w').write(str(os.getpid())); time.sleep(60)"
    slow = [sys.executable, "-c", code]
    t0 = time.monotonic()
    with pytest.raises(media.MediaTimeout) as err:
        media._run(slow, timeout=2.0)
    assert time.monotonic() - t0 < 20
    assert isinstance(err.value, TimeoutError) and isinstance(err.value, media.MediaError)
    assert "2 s" in str(err.value)
    assert not _alive(int(pid_file.read_text()))


def test_every_tool_runs_with_a_limit_that_grows_with_the_media(tone_slides, tmp_path,
                                                                monkeypatch):  # fmt: skip
    """ffmpeg gets max(30 s, 8 x the media it handles); a probe gets 30 s."""
    limits: list = []
    real = media._exec

    def spy(cmd, *a, **kw):
        limits.append((Path(cmd[0]).stem.lower(), kw.get("timeout")))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(media, "_exec", spy)
    media.probe(tone_slides)
    media.cut_clip(tone_slides, tmp_path / "c.mp4", 1.0, 2.0, height=180)
    media.extract_wav(tone_slides, tmp_path / "a.wav", seconds=100.0)
    assert limits == [("ffprobe", 30.0), ("ffmpeg", 30.0), ("ffmpeg", 800.0)]
    assert media.ffmpeg_timeout(60.0) == 480.0 and media.ffmpeg_timeout(None) == 30.0


def test_a_tool_that_times_out_takes_the_processes_it_started_with_it(tmp_path):
    """ffmpeg can start helpers; on expiry the whole process tree is killed (a new session
    killed as a group on POSIX, taskkill /T /F on Windows), not only the direct child."""
    import sys

    from lull.stages.publish import _alive

    pids = tmp_path / "pids"
    grandchild = "import time; time.sleep(60)"
    code = (
        "import os, subprocess, sys, time; "
        f"g = subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        f"open(r'{pids}', 'w').write(f'{{os.getpid()}} {{g.pid}}'); time.sleep(60)"
    )
    import time

    t0 = time.monotonic()
    with pytest.raises(media.MediaTimeout):
        media._run([sys.executable, "-c", code], timeout=3.0)
    assert time.monotonic() - t0 < 20  # not held up until the helper's own end (60 s)
    child, grand = (int(p) for p in pids.read_text().split())
    assert not _alive(child) and not _alive(grand)


def test_the_wait_after_a_kill_is_bounded_too(monkeypatch):
    """A process whose pipes never close after the kill cannot hang the run: the wait after
    the kill has a limit of its own, and the timeout is raised."""
    waits = []

    class Hung:
        pid, returncode = 4242, None

        def communicate(self, timeout=None):
            waits.append(timeout)
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

        def poll(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(media.subprocess, "Popen", lambda *a, **k: Hung())
    monkeypatch.setattr(media, "_kill_tree", lambda proc: None)
    with pytest.raises(subprocess.TimeoutExpired):
        media._exec(["ffmpeg"], cwd=None, timeout=1)
    assert len(waits) == 2 and None not in waits


def test_probe_knows_where_the_picture_ends(long_audio):
    info = media.probe(long_audio)
    assert info.duration > 3.5  # the file: its sound runs to 4 s
    assert info.video_end == pytest.approx(1.9, abs=0.06)  # the picture's last frame


def test_the_last_frame_of_the_picture_can_be_taken(long_audio, tmp_path):
    out = media.last_frame(long_audio, tmp_path / "last.jpg", window=4.0)
    assert out.stat().st_size > 0
