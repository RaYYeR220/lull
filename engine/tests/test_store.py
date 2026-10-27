import json
from pathlib import Path

import pytest

from lull.stages.listen import ClampedWord, ListenResult
from lull.stages.plan import PlanResult
from lull.stages.research import GlossaryEntry, ResearchResult
from lull.stages.schedule import Placement
from lull.store import Store, file_sha256, stage_key
from lull.types import Claim, Cue, Gap, Span, Turn, Variant, VisualEvent, Word


def test_saved_data_comes_back_only_for_the_same_key(tmp_path):
    store = Store(tmp_path / "run")
    assert store.cached("listen", "k1") is None
    store.save("listen", "k1", {"words": [1, 2], "x": 0.1})
    assert store.cached("listen", "k1") == {"words": [1, 2], "x": 0.1}
    assert store.cached("listen", "k2") is None  # inputs changed: recompute
    on_disk = json.loads((tmp_path / "run" / "stages" / "listen.json").read_text("utf-8"))
    assert on_disk == {"key": "k1", "data": {"words": [1, 2], "x": 0.1}}
    store.save("listen", "k2", {"words": []})
    assert store.cached("listen", "k1") is None and store.cached("listen", "k2") == {"words": []}


def test_nested_stage_names_are_files_under_their_folder(tmp_path):
    store = Store(tmp_path)
    store.save("finalize/c001", "k", {"id": "c001"})
    assert (tmp_path / "stages" / "finalize" / "c001.json").exists()
    assert store.cached("finalize/c001", "k") == {"id": "c001"}
    assert not list((tmp_path / "stages").rglob("*.tmp"))  # written atomically


@pytest.mark.parametrize("bad", ["../x", "a b", "", "finalize/", "/abs", "a\\b", "x.json"])
def test_unsafe_stage_names_are_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        Store(tmp_path).save(bad, "k", {})


def test_a_damaged_cache_file_is_a_miss(tmp_path, caplog):
    store = Store(tmp_path)
    path = tmp_path / "stages" / "watch.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"key": "k", "data": {', encoding="utf-8")  # a crash mid-write elsewhere
    assert store.cached("watch", "k") is None
    assert "watch" in caplog.text
    path.write_text('["not", "an", "entry"]', encoding="utf-8")
    assert store.cached("watch", "k") is None


def test_stage_key_is_canonical(tmp_path):
    a = stage_key({"b": 1, "a": [Span(start=0.0, end=1.5)], "s": {"y", "x"}, "p": Path("a/b")})
    b = stage_key({"p": Path("a/b"), "s": {"x", "y"}, "a": [{"start": 0.0, "end": 1.5}], "b": 1})
    assert a == b and len(a) == 64 and int(a, 16) >= 0
    assert stage_key({"b": 2}) != stage_key({"b": 1})
    assert stage_key({"x": 1.0}) != stage_key({"x": 1.0000001})
    with pytest.raises(TypeError):
        stage_key({"f": object()})


def test_file_sha256(tmp_path):
    p = tmp_path / "v.bin"
    p.write_bytes(b"abc")
    assert file_sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_every_stage_result_round_trips_through_json():
    word = Word(text="Emo,", start=1.25, end=1.5, speaker=2)
    gap = Gap(
        id="g001",
        start=1.8,
        end=6.2,
        usable_start=2.0,
        usable_end=6.0,
        capacity_words=9,
        protected=[Span(start=3.0, end=3.4)],
    )
    listen = ListenResult(
        words=[word],
        turns=[Turn(speaker=2, start=1.2, end=1.6)],
        speech=[Span(start=1.2, end=1.6)],
        gaps=[gap],
        protected=[Span(start=3.0, end=3.4)],
        duration=12.0,
        has_audio=True,
        clamped=[ClampedWord(text="way.", start=1.0, end=9.0, clamped_end=1.61)],
    )
    event = VisualEvent(
        id="e001",
        t_start=0.0,
        t_end=6.0,
        kind="slide",
        text="A slide.",
        verbatim_text="Results 2026",
        salience=5,
        said_in_dialogue=False,
    )
    research = ResearchResult(
        glossary=[GlossaryEntry(term="emo", canonical="Emo", note="a film", sources=["https://x"])],
        skipped=False,
        source_context="ctx",
        errors=["search failed for 'x'"],
    )
    plan = PlanResult(
        variants=[Variant(event_id="e001", length="short", text="A slide.", words=2)],
        cast={"Emo": "a young person in a cap"},
        priority={"e001": 5},
    )
    placement = Placement(event_id="e001", variant="short", gap_id="g001", ahead=True)
    cue = Cue(
        id="c001",
        gap_id="g001",
        start=2.0,
        end=3.5,
        text="A slide.",
        event_ids=["e001"],
        status="verified",
        ahead=False,
        claims=[Claim(text="a slide", verdict="supported", at=2.5)],
        audio_path="work/audio/c001.wav",
        audio_dur=1.5,
        tempo=1.05,
        findings=[{"rule": "R14-gender", "message": "m", "span": [0, 3], "severity": "warning"}],
        note="compressed 9→6 words",
    )
    for model in (listen, event, research, plan, placement, cue):
        data = json.loads(json.dumps(model.model_dump(mode="json")))
        assert type(model).model_validate(data) == model
