import { Figtree, IBM_Plex_Mono, Newsreader } from "next/font/google";

/** Display: variable, optical size axis, roman. Preloaded: the headings are the first paint. */
export const newsreader = Newsreader({
  subsets: ["latin"],
  style: ["normal"],
  axes: ["opsz"],
  display: "swap",
  variable: "--font-newsreader",
});

/**
 * The voice: the same family's italic, declared apart so it isn't preloaded (it is for spoken
 * lines and quotes, never the first paint). Both declare the family "Newsreader", so text set
 * in italic finds these faces through --font-newsreader.
 */
export const newsreaderItalic = Newsreader({
  subsets: ["latin"],
  style: ["italic"],
  axes: ["opsz"],
  display: "swap",
  preload: false,
  variable: "--font-newsreader-italic",
});

/** Body and interface. */
export const figtree = Figtree({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-figtree",
});

/** Timecodes, file names and code only. Not needed for first paint. */
export const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  preload: false,
  variable: "--font-plex-mono",
});
