"use client";

import { useEffect, useRef, type CSSProperties, type DetailedHTMLProps, type HTMLAttributes } from "react";
import { cx } from "@/lib/cx";
import type { CueEventDetail, ModeEventDetail, PlayerMode } from "@/player/src/lull-player";
import styles from "./Player.module.css";

export type { CueEventDetail, ModeEventDetail, PlayerMode };

export type PlayerProps = {
  /** The video. */
  src: string;
  /** WebVTT, kind descriptions: the cue source for spoken cues and the screen reader mode. */
  descriptions?: string;
  /** The mixed described soundtrack (m4a); enables the mixed audio mode. */
  describedAudio?: string;
  /** extended.vtt; enables the extended mode. */
  extended?: string;
  /** Where the per-cue recordings live: `<base>/<cue id>.wav`, or a pattern with `{id}`. */
  cueAudioBase?: string;
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
};

type LullPlayerAttributes = DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
  src?: string;
  descriptions?: string;
  "described-audio"?: string;
  extended?: string;
  "cue-audio-base"?: string;
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
  captions,
  poster,
  mode,
  label,
  crossOrigin,
  className,
  style,
  onModeChange,
  onCue,
}: PlayerProps) {
  const ref = useRef<HTMLElement>(null);
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
      ref={ref}
      className={cx(styles.player, className)}
      style={style}
      src={src}
      descriptions={descriptions}
      described-audio={describedAudio}
      extended={extended}
      cue-audio-base={cueAudioBase}
      captions={captions}
      poster={poster}
      mode={mode}
      label={label}
      crossorigin={crossOrigin}
    />
  );
}
