import { Figtree, IBM_Plex_Mono, Newsreader } from "next/font/google";

/** Display and the voice: variable, optical size axis, roman and italic. */
export const newsreader = Newsreader({
  subsets: ["latin"],
  style: ["normal", "italic"],
  axes: ["opsz"],
  display: "swap",
  variable: "--font-newsreader",
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
