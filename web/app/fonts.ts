import { Figtree, IBM_Plex_Mono, Newsreader } from "next/font/google";

/**
 * Display, roman: the two static weights the site sets (300, and 400 for small titles and pause
 * labels), latin only, no optical-size axis, so the first paint waits on far fewer bytes.
 * Preloaded: the headings are the first paint.
 */
export const newsreader = Newsreader({
  subsets: ["latin"],
  style: ["normal"],
  weight: ["300", "400"],
  display: "swap",
  variable: "--font-newsreader",
});

/**
 * The voice: the same family's italic, declared apart so it isn't preloaded but fetched when
 * italic text first shows (it is for spoken lines and quotes, never the headings). Both declare the family "Newsreader", so text set
 * in italic finds these faces through --font-newsreader.
 */
export const newsreaderItalic = Newsreader({
  subsets: ["latin"],
  style: ["italic"],
  // one static weight: the spoken lines' 350 renders at 300
  weight: ["300"],
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
