import type { SVGProps } from "react";

/**
 * The shared glyphs: 12-unit grid, square caps, drawn in currentColor.
 * Always decorative; the word beside a glyph carries the meaning.
 */
const GLYPHS = {
  ok: <path d="M1.5 6.4 4.6 9.4 10.5 2.6" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="square" />,
  out: <path d="M2 2l8 8M10 2l-8 8" fill="none" stroke="currentColor" strokeWidth="1.7" />,
  held: <path d="M3.5 1.5v9M8.5 1.5v9" fill="none" stroke="currentColor" strokeWidth="2" />,
  edit: (
    <path
      d="M1.5 10.5l1-3.2L8.6 1.2l2.2 2.2-6.1 6.1zM7.2 2.6l2.2 2.2"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="miter"
    />
  ),
  /* a check signed off on a rule: a person approved the line */
  approved: (
    <path d="M1.5 5.2 4.4 8 10.5 1.4M1.5 10.9h9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="square" />
  ),
  /* the pause pushed wider: the video waits for the line */
  extended: (
    <path
      d="M1 1.5v9M11 1.5v9M3.2 6h5.6M5 4.2 3.2 6 5 7.8M7 4.2 8.8 6 7 7.8"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
    />
  ),
  play: <path d="M3 1.8v8.4L10 6z" fill="currentColor" />,
  pause: <path d="M3 1.8h2v8.4H3zM7 1.8h2v8.4H7z" fill="currentColor" />,
  replay: (
    <>
      <path d="M2.4 6a3.6 3.6 0 1 0 1.1-2.6" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M2 1.4v3h3" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </>
  ),
  /* a speaker: something is spoken. Drawn on a 14 x 12 grid */
  voice: (
    <>
      <path d="M1 4.2h2.6L7 1.2v9.6L3.6 7.8H1z" fill="currentColor" />
      <path d="M9.4 3.6a3.4 3.4 0 0 1 0 4.8M11.4 1.8a6 6 0 0 1 0 8.4" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </>
  ),
} as const;

/** Glyphs wider than the 12-unit square. */
const WIDTH: Partial<Record<keyof typeof GLYPHS, number>> = { voice: 14 };

export type GlyphName = keyof typeof GLYPHS;

type GlyphProps = Omit<SVGProps<SVGSVGElement>, "children" | "viewBox"> & {
  name: GlyphName;
  size?: number;
};

/** `size` is the height; wide glyphs keep their proportions. */
export function Glyph({ name, size = 12, ...rest }: GlyphProps) {
  const w = WIDTH[name] ?? 12;
  return (
    <svg width={(size * w) / 12} height={size} viewBox={`0 0 ${w} 12`} aria-hidden="true" focusable="false" {...rest}>
      {GLYPHS[name]}
    </svg>
  );
}
