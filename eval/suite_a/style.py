"""The fixed look and timing of every Suite A lecture: one place, so the renderer, the spec
validator and the answer key's slide inventory always agree."""

from __future__ import annotations

import math

WIDTH, HEIGHT, DPI = 1280, 720, 100
FPS = 25
FONT = "DejaVu Sans"  # bundled with matplotlib: the same glyphs on every machine

BACKGROUND = "#ffffff"
TITLE_COLOR = "#1b2a4a"  # dark blue
TEXT_COLOR = "#333333"  # dark grey
ACCENT = "#2a6fb0"  # blue: the rule under the title, bars and lines
GRID = "#dddddd"
MUTED = "#7a7a7a"  # slide number

TITLE_PT, BULLET_PT, CHART_TITLE_PT, AXIS_PT, TICK_PT, VALUE_PT, NUMBER_PT = (
    30, 20, 16, 14, 13, 14, 12,
)  # fmt: skip

NARRATOR_VOICE = "Aria"  # Lull describes in "Sofia": two different voices
LEAD_IN_S = 0.0  # the first slide is on screen from 0 s; its first pause is the spec's
TAIL_S = 2.0  # silence after the last sentence
TRIM_DB = -45.0  # TTS leading/trailing silence below this (dBFS, 10 ms frames) is cut
TRIM_PAD_S = 0.02  # kept around the voiced part

STYLE_TEXT = (
    "Every slide is static for its whole time on screen (no animation, no transitions): "
    "a white background; at the top the slide title in large bold dark-blue text, underlined "
    "by a thin blue horizontal rule across the slide; a bulleted list in dark grey with a "
    "round bullet before each item (left half when the slide has a chart, else full width); "
    "small grey slide number 'k / n' in the bottom-right corner. A chart sits on the right "
    "half: its title in dark text above the plot, blue vertical bars (bar chart) or a blue "
    "line with round markers (line chart), the value of every bar or point printed just "
    "above it, category labels along the horizontal axis, a horizontal-axis label, a "
    "vertical-axis label, vertical-axis tick numbers starting at 0, light grey horizontal "
    "grid lines, no legend. Nothing else is ever on screen: no people, photographs, icons, "
    "logos, drawings or other images. The narration is one off-screen lecturer (a synthetic "
    "voice); no speaker is ever visible."
)


def y_ticks(values: list[float]) -> tuple[float, list[float]]:
    """The vertical axis: its top and its tick values, from 0 in a 1/2/2.5/5 x 10^k step so
    that there are at most six ticks. The top leaves room for the value printed above the
    highest bar or point."""
    vmax = max(max(values), 1e-9)
    top = vmax * 1.18
    raw = top / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw - 1e-12)
    n = int(math.floor(top / step + 1e-9))
    ticks = [round(i * step, 6) for i in range(n + 1)]
    return top, ticks
