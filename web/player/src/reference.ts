/**
 * The player's public surface as data: its attributes, its modes and its keyboard map. The
 * element reads them, and the developer docs print them, so the two can't drift apart.
 */

export type PlayerMode = "mixed" | "spoken" | "screen-reader" | "extended";
export const MODES: readonly PlayerMode[] = ["mixed", "spoken", "screen-reader", "extended"];

/** Every attribute the element observes (a change at runtime reloads that input). */
export const PLAYER_ATTRIBUTES = [
  "src",
  "descriptions",
  "described-audio",
  "extended",
  "cue-audio-base",
  "cue-audio-ext",
  "captions",
  "poster",
  "mode",
  "label",
  "crossorigin",
  "sound",
] as const;
export type PlayerAttribute = (typeof PLAYER_ATTRIBUTES)[number];

export const MODE_NAME: Record<PlayerMode, string> = {
  mixed: "Mixed audio",
  spoken: "Spoken cues",
  "screen-reader": "My screen reader",
  extended: "Extended",
};

export const MODE_DESC: Record<PlayerMode, string> = {
  mixed: "The described soundtrack, mixed in advance. The video’s own sound is off.",
  spoken: "The original soundtrack. Each line is spoken in its pause and the sound dips under it.",
  "screen-reader": "Each line goes to your screen reader as it comes up. Nothing is spoken here.",
  extended: "Like spoken cues, and the video waits while a longer line is spoken.",
};

/** The keyboard map, while the focus is in the player: keys, and what they do. */
export const KEYS: readonly [readonly string[], string][] = [
  [["K", "Space"], "Play or pause"],
  [["←", "→"], "Back or ahead 5 s"],
  [["↑", "↓"], "Volume"],
  [["M"], "Description mode"],
  [["C"], "Captions"],
  [["T"], "Transcript"],
  [["F"], "Full screen"],
  [["<", ">"], "Slower or faster"],
  [["Home", "End"], "Start or end, on the seek bar"],
  [["?"], "Show or hide this list"],
];
