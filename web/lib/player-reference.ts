import type { PlayerAttribute } from "@/player/src/reference";

/** What each <lull-player> attribute does, as the element reads it (player/src/lull-player.ts). */
export const ATTRIBUTE_DOCS: Record<PlayerAttribute, { values: string; what: string }> = {
  src: {
    values: "URL",
    what: "The video. When it is missing or fails, the player says so and the transcript still works.",
  },
  descriptions: {
    values: "URL of a WebVTT file",
    what: "The description track (descriptions.vtt): the lines for spoken cues, the screen reader mode, the transcript and the timeline.",
  },
  "described-audio": {
    values: "URL of an audio file",
    what: "The soundtrack with the lines mixed in (described-audio.m4a). Turns on mixed audio.",
  },
  extended: {
    values: "URL of a WebVTT file",
    what: "The extended lines (extended.vtt), each after a NOTE extended pause=<seconds>. Turns on extended: the video waits while each is said, at most 30 s.",
  },
  "cue-audio-base": {
    values: "folder URL ending in /, or a pattern with {id}",
    what: "Where each line's own recording is: <base><cue id>.<cue-audio-ext>, or the pattern with {id} replaced. Without it, spoken cues use the browser's voice.",
  },
  "cue-audio-ext": {
    values: "m4a (default), wav, …",
    what: "The recordings' file extension. A missing file is tried once as .wav.",
  },
  captions: {
    values: "URL of a WebVTT file",
    what: "Captions, toggled with C. Without it the captions button isn't shown.",
  },
  poster: {
    values: "URL of an image",
    what: "The picture shown before playing.",
  },
  mode: {
    values: "mixed, spoken, screen-reader, extended",
    what: "The mode to start in. Left out, the viewer's last choice on your site is used, else the first mode the files allow.",
  },
  label: {
    values: "text",
    what: "The player's accessible name, e.g. the video's title (default “Described video”).",
  },
  crossorigin: {
    values: "anonymous, use-credentials",
    what: "Set when the media is on another origin that sends CORS headers: the sound can then dip under each spoken line.",
  },
  sound: {
    values: "described",
    what: "The video's own sound already carries the descriptions (a described.mp4): only mixed audio is offered, so no line is said twice.",
  },
};
