"""The one place the controls touch the engine's frame verification.

Everything engine-facing the controls need goes through here: the verification clip window,
cutting the clip, the verifier call and the switch that turns verification off. If the engine
changes the signature of `lull.stages.verify.verify` (or of these helpers), this file is the
one-place edit; it is deliberately NOT pinned by the controls' tags (negative.py, ablation.py
and prereg.json are), so following the engine never breaks the pin.

Written against the final engine (main 61e9d81, merged 2026-10-28):

    verify(text, clip, tf, *, run_id, names=(), offset=0.0, cache=None, dropped=None,
           notes=None) -> list[Claim]
    clip_window(events, duration, *, pad, cap, least) -> (lo, hi)

`verify` splits the line into atomic claims (Nemotron 3.5 Lightning, prompts/split.md,
temperature 0; token ranges checked, a failed split falls back to the sentences), adds every
whole sentence of the line (the sentence gate), and judges all of them against the clip
(Nemotron 3.5 Super VL, prompts/verify.md, temperature 0); supported claims the line does not
make are left out. Each returned claim has a kind: "atomic" or "sentence". An answer cut off by
its token limit raises TFUnusable: the pipeline then holds the cue, so the control counts it
as not accepted. The controls pass no names: the pipeline passes the cast names a line uses
as context, which a cue checked against other footage has no claim to.
"""

from __future__ import annotations

from pathlib import Path

NO_VERIFY_ENV = "LULL_NO_VERIFY"  # the engine switch the ablation relies on
CUT_OFF = "(verification answer cut off)"


def _verify_module():
    from lull.stages import verify as V

    return V


def settings() -> dict:
    """The verifier's models and settings, for the record and the controls' cache key."""
    from lull.tf import LIGHTNING, VL

    V = _verify_module()
    return {"model": VL, "temperature": V.VERIFY_TEMPERATURE, "max_tokens": V.VERIFY_MAX_TOKENS,
            "split_model": LIGHTNING, "split_temperature": V.SPLIT_TEMPERATURE,
            "split_max_tokens": V.SPLIT_MAX_TOKENS, "sentence_gate": True,
            "clip_pad_s": V.CLIP_PAD_S, "clip_cap_s": V.CLIP_CAP_S, "clip_min_s": V.CLIP_MIN_S,
            "clip_see_s": V.CLIP_SEE_S, "clip_max_s": V.CLIP_MAX_S,
            "clip_height": V.CLIP_HEIGHT, "thinking": False}  # fmt: skip


def clip_window(events: list[dict], duration: float) -> tuple[float, float]:
    """The engine's verification window for a cue's watch events (dicts from watch.json)."""
    V = _verify_module()
    return V.clip_window([V.VisualEvent(**e) for e in events], duration)


def cut(video: Path, dst: Path, lo: float, hi: float) -> Path:
    """The clip [lo, hi] of `video` as the pipeline cuts it for the verifier."""
    from lull.media import cut_clip

    dst.parent.mkdir(parents=True, exist_ok=True)
    return cut_clip(video, dst, lo, hi, height=_verify_module().CLIP_HEIGHT)


def check(text: str, clip: Path, *, offset: float, tf, run_id: str) -> list[dict]:
    """The verifier's claims for `text` against `clip` (which starts at `offset` in its
    video), as plain dicts {text, verdict, at, kind}; an answer cut off by its token limit is
    one not-visible entry (the pipeline would hold the cue)."""
    from lull.tf import TFUnusable

    V = _verify_module()
    try:
        claims = V.verify(text, clip, tf, run_id=run_id, names=(), offset=offset)
    except TFUnusable:
        return [{"text": CUT_OFF, "verdict": "not_visible", "at": None, "kind": "cut_off"}]
    return [{"text": c.text, "verdict": c.verdict, "at": c.at,
             "kind": getattr(c, "kind", "atomic")} for c in claims]  # fmt: skip


def verification_disabled(manifest: dict) -> bool:
    """Whether a run's manifest records that verification was switched off."""
    flag = manifest.get("verification", (manifest.get("summary") or {}).get("verification"))
    return flag == "disabled"
