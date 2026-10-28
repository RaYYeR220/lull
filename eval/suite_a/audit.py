"""Audit tables for the README, computed from key.json (no model, no network):

    uv run python -m suite_a.audit

1. per lecture: length, the seconds and pauses without narration, the facts;
2. the word-overlap borderline cases: on-slide text the rules called "partly said" (not
   scored), visual facts that still share some words with the slide's narration, and spoken
   facts the narration says in other words (by overlap, not as a phrase);
3. charts whose highest value is tied.
"""

from __future__ import annotations

from suite_a.score import chart_ties, load_key, silence
from suite_a.text import contains_phrase, overlap

NEAR = 0.25  # a visual fact sharing at least this share of its words is listed
TEXT_KINDS = ("title", "bullet", "label")  # a chart_value anchor is a category, not the fact


def slide_narration(entry: dict, k: int) -> str:
    return " ".join(s["text"] for s in entry["narration"] if s["slide"] == k)


def main() -> None:
    key, _ = load_key()
    print("| lecture | subject | s | silence s | pauses | pauses >= 2 s | visual | spoken |")
    print("|---|---|---|---|---|---|---|---|")
    for v in key["videos"]:
        s = silence(v)
        print(f"| {v['id']} | {v['subject']} | {v['duration']:.1f} | {s['seconds']:.1f} | "
              f"{s['pauses']} | {s['pauses_ge_2s']} | {len(v['visual_facts'])} | "
              f"{len(v['spoken_facts'])} |")  # fmt: skip
    print()
    print("| lecture | slide | rule | kind | on-slide text | share of words said |")
    print("|---|---|---|---|---|---|")
    for v in key["videos"]:
        everywhere = " ".join(s["text"] for s in v["narration"])
        rows = [("partly said, not scored", f) for f in v["partly_said"]]
        rows += [
            ("visual, shares words", f)
            for f in v["visual_facts"]
            if f["kind"] in TEXT_KINDS
            and overlap(f["anchor"], slide_narration(v, f["slide"])) >= NEAR
        ]
        rows += [("spoken by overlap", f) for f in v["spoken_facts"]
                 if not contains_phrase(everywhere, f["anchor"])]  # fmt: skip
        for rule, f in rows:
            share = overlap(f["anchor"], slide_narration(v, f["slide"]))
            print(f"| {v['id']} | {f['slide']} | {rule} | {f['kind']} | {f['anchor']} | "
                  f"{share:.2f} |")  # fmt: skip
    print()
    for v in key["videos"]:
        for fid, labels in chart_ties(v).items():
            print(f"- {fid}: the highest value is also shown for {', '.join(labels)}")


if __name__ == "__main__":
    main()
