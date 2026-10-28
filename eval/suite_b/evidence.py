"""Suite B ASR corrections: the on-screen text of frames of the ORIGINAL USGS videos, read by
Nemotron 3.5 Super VL, as evidence for refs/asr_corrections.json.

    uv run --directory eval python -m suite_b.evidence --video woodshole --times 5.5,36.5

Each frame is extracted with ffmpeg at the given time (full resolution, JPEG quality 2) into
out/evidence/, read once (temperature 0, thinking off; answers cached by frame sha256 and
prompt) and written next to it as JSON: the video file, the time, the frame's sha256, the
model and the text. A correction cites one of these files; refs.py accepts it only when the
corrected term occurs in the cited text. Lull's outputs are never read here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from suite_b.refs import DATA_DEFAULT, OUT, VIDEOS, sha256_file, write_json

EVIDENCE = OUT / "evidence"
RUN_ID = "suite-b-evidence"
ESTIMATE = 0.01
CAP = 0.10
PROMPT = (
    "Transcribe every piece of text visible in this video frame, exactly as written (spelling, "
    "capitals, numbers, punctuation), including captions, signs, titles, labels, menus, tabs "
    "and legends. One item per line, top to bottom, left to right. Do not describe the "
    "picture. If there is no text, answer NONE."
)


def frame(src: Path, t: float, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src),
                    "-frames:v", "1", "-q:v", "2", str(dst)], check=True)  # fmt: skip
    return dst


def read_frame(tf, ledger, model: str, path: Path) -> dict:
    from lull.tf import image_part

    sha = sha256_file(path)
    key = hashlib.sha256(f"{model}\n{PROMPT}\n{sha}".encode()).hexdigest()
    cache = EVIDENCE / "cache" / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if ledger.spent(run_id=RUN_ID) + ESTIMATE > CAP:
        raise SystemExit(f"evidence cap ${CAP:.2f} reached")
    content = [image_part(path), {"type": "text", "text": PROMPT}]
    text = tf.text(model, [{"role": "user", "content": content}], run_id=RUN_ID,
                   stage="suite_b_evidence", role="eval-evidence", thinking=False,
                   max_tokens=3000, temperature=0.0, estimate=ESTIMATE)  # fmt: skip
    out = {"frame_sha256": sha, "model": model, "prompt": PROMPT, "text": text.strip()}
    write_json(cache, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", required=True, choices=[v for v, s in VIDEOS.items()
                                                       if s["kind"] == "usgs"])  # fmt: skip
    ap.add_argument("--times", required=True, help="comma-separated seconds in the original")
    ap.add_argument("--data", type=Path, default=DATA_DEFAULT)
    args = ap.parse_args(argv)
    from lull.config import get_settings
    from lull.ledger import Ledger
    from lull.tf import VL, TokenFactory

    ledger = Ledger(get_settings().lull_home / "ledger.db", cap_run=CAP)
    tf = TokenFactory.from_settings(ledger)
    rel = VIDEOS[args.video]["media"]["file"]
    src = args.data / rel
    for t in (float(x) for x in args.times.split(",")):
        img = frame(src, t, EVIDENCE / f"{args.video}_{t:07.2f}.jpg")
        got = read_frame(tf, ledger, VL, img)
        doc = {"video": args.video, "file": rel, "time_s": t, **got}
        write_json(img.with_suffix(".json"), doc)
        print(f"--- {args.video} {t:.2f} s (frame {got['frame_sha256'][:12]})\n{got['text']}\n")
    print(f"evidence spend ${ledger.spent(run_id=RUN_ID):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
