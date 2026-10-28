"use client";

import { useCallback, useEffect, useRef, type CSSProperties, type DetailedHTMLProps, type HTMLAttributes, type Ref } from "react";
import { cx } from "@/lib/cx";
import type { CueEventDetail, ModeEventDetail, PlayerMode, PlayerState } from "@/player/src/lull-player";
import styles from "./Player.module.css";

export type { CueEventDetail, ModeEventDetail, PlayerMode, PlayerState };

/**
 * The element's own API, for pages that drive it. Until its code has loaded the element is a
 * plain HTMLElement, so `state` and the methods may be missing: check before calling.
 */
export type PlayerElement = HTMLElement & {
  readonly state?: PlayerState;
  readonly currentTime?: number;
  seek?(seconds: number): void;
  play?(): void;
  pause?(): void;
};

export type PlayerProps = {
  /** The video. */
  src: string;
  /** WebVTT, kind descriptions: the cue source for spoken cues and the screen reader mode. */
  descriptions?: string;
  /** The mixed described soundtrack (m4a); enables the mixed audio mode. */
  describedAudio?: string;
  /** extended.vtt; enables the extended mode. */
  extended?: string;
  /** Where the per-cue recordings live: `<base>/<cue id>.<cueAudioExt>` (a .wav is tried after), or a pattern with `{id}`. */
  cueAudioBase?: string;
  /** The recordings' extension; "m4a" (the engine's export) when left out. */
  cueAudioExt?: string;
  captions?: string;
  poster?: string;
  /** The mode to start in; a viewer's own earlier choice wins when this is left out. */
  mode?: PlayerMode;
  /** The player's accessible name, e.g. the video title. */
  label?: string;
  /** Set when the media is on another origin that sends CORS headers. */
  crossOrigin?: "anonymous" | "use-credentials";
  className?: string;
  style?: CSSProperties;
  onModeChange?: (detail: ModeEventDetail) => void;
  onCue?: (detail: CueEventDetail) => void;
  /** The <lull-player> element. */
  ref?: Ref<PlayerElement>;
};

type LullPlayerAttributes = DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
  src?: string;
  descriptions?: string;
  "described-audio"?: string;
  extended?: string;
  "cue-audio-base"?: string;
  "cue-audio-ext"?: string;
  captions?: string;
  poster?: string;
  mode?: string;
  label?: string;
  crossorigin?: string;
};

declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      "lull-player": LullPlayerAttributes;
    }
  }
}

/**
 * The site's way to place a <lull-player>: it loads the element's code on the client and
 * passes the props through as attributes. The same element ships alone as /player.js.
 */
export function Player({
  src,
  descriptions,
  describedAudio,
  extended,
  cueAudioBase,
  cueAudioExt,
  captions,
  poster,
  mode,
  label,
  crossOrigin,
  className,
  style,
  onModeChange,
  onCue,
  ref: outer,
}: PlayerProps) {
  const ref = useRef<HTMLElement>(null);
  // the element goes to the wrapper's own ref and to the caller's
  const setRef = useCallback(
    (el: HTMLElement | null) => {
      ref.current = el;
      if (typeof outer === "function") outer(el);
      else if (outer) outer.current = el;
    },
    [outer],
  );
  const handlers = useRef({ onModeChange, onCue });
  useEffect(() => {
    handlers.current = { onModeChange, onCue };
  });

  useEffect(() => {
    void import("@/player/src/lull-player");
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const mode = (e: Event) => handlers.current.onModeChange?.((e as CustomEvent<ModeEventDetail>).detail);
    const cue = (e: Event) => handlers.current.onCue?.((e as CustomEvent<CueEventDetail>).detail);
    el.addEventListener("lull-modechange", mode);
    el.addEventListener("lull-cue", cue);
    return () => {
      el.removeEventListener("lull-modechange", mode);
      el.removeEventListener("lull-cue", cue);
    };
  }, []);

  return (
    <lull-player
      ref={setRef}
      className={cx(styles.player, className)}
      style={style}
      src={src}
      descriptions={descriptions}
      described-audio={describedAudio}
      extended={extended}
      cue-audio-base={cueAudioBase}
      cue-audio-ext={cueAudioExt}
      captions={captions}
      poster={poster}
      mode={mode}
      label={label}
      crossorigin={crossOrigin}
    />
  );
}
