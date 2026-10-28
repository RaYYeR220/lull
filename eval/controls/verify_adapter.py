"""The one place the controls touch the engine's frame verification.

Everything engine-facing the controls need goes through here: the verification clip window,
cutting the clip, the verifier call and the switch that turns verification off. If the engine
changes the signature of `lull.stages.verify.verify` (or of these helpers), this file is the
one-place edit; it is deliberately NOT pinned by the `controls-prereg` tag (negative.py,
ablation.py and prereg.json are), so following the engine never breaks the pin.

Written against the engine on main as of 2026-10-28:

    verify(text, clip, tf, *, run_id, names=(), offset=0.0) -> list[Claim]
    clip_window(events, duration, *, pad, cap, least) -> (lo, hi)

`verify` sends the clip to Nemotron 3.5 Super VL with the pipeline's own prompt and settings
(the module constants: temperature, max tokens), splits the line into atomic claims and judges
each supported / not_visible / contradicted; supported claims the line does not make are left
out. The controls pass no names: the pipeline passes the cast names a line uses as context,
which a cue checked against other footage has no claim to.
"""

from __future__ import annotations

from pathlib import Path

NO_VERIFY_ENV = "LULL_NO_VERIFY"  # the engine switch the ablation relies on (main, after merge)


def _verify_module():
    from lull.stages import verify as V

    return V


def settings() -> dict:
    """The verifier's model and settings, for the record."""
    from lull.tf import VL

    V = _verify_module()
    return {"model": VL, "temperature": V.VERIFY_TEMPERATURE, "max_tokens": V.VERIFY_MAX_TOKENS,
            "clip_pad_s": V.CLIP_PAD_S, "clip_cap_s": V.CLIP_CAP_S, "clip_min_s": V.CLIP_MIN_S,
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
    video), as plain dicts {text, verdict, at}."""
    V = _verify_module()
    claims = V.verify(text, clip, tf, run_id=run_id, names=(), offset=offset)
    return [{"text": c.text, "verdict": c.verdict, "at": c.at} for c in claims]


def verification_disabled(manifest: dict) -> bool:
    """Whether a run's manifest records that verification was switched off."""
    flag = manifest.get("verification", (manifest.get("summary") or {}).get("verification"))
    return flag == "disabled"
