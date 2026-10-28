"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Player, type PlayerElement } from "@/components/player/Player";
import { CueScript, type ScriptCue } from "./CueScript";
import styles from "./Watch.module.css";

export type WatchMedia = {
  src: string;
  descriptions: string;
  extended: string;
  describedAudio?: string;
  cueAudioBase?: string;
  cueAudioExt: string;
};

/** How often the script checks which line the player is on. */
const FOLLOW_MS = 250;

/**
 * A recorded run on its page. On wide screens the picture comes first, with the run's name
 * and its actions beside it, and the script below, which follows the player and moves it;
 * the checks and the files sit beside the script. Narrow screens take them in DOM order.
 */
export function WatchRun({
  media,
  label,
  cues,
  head,
  edit,
  files,
  checks,
}: {
  media: WatchMedia;
  label: string;
  cues: ScriptCue[];
  head: ReactNode;
  edit: ReactNode;
  files: ReactNode;
  checks: ReactNode;
}) {
  const player = useRef<PlayerElement>(null);
  const [active, setActive] = useState<string | null>(null);

  // the player says which line is on (a standard line in its span, or an extended line
  // while the picture holds for it); follow it while the page is visible
  useEffect(() => {
    let last: string | null = null;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      const now = player.current?.state?.activeCue ?? null;
      if (now !== last) {
        last = now;
        setActive(now);
      }
    }, FOLLOW_MS);
    return () => window.clearInterval(id);
  }, []);

  const seek = useCallback((cue: ScriptCue) => {
    const el = player.current;
    if (!el?.seek) return;
    el.seek(cue.start);
    // bring the picture into view when the list has scrolled it away; focus stays on the line
    const r = el.getBoundingClientRect();
    if (r.bottom < 80 || r.top > window.innerHeight - 80) {
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
    }
  }, []);

  return (
    <div className={styles.run}>
      <div className={styles.head}>{head}</div>
      <div className={styles.screen}>
        <Player
          ref={player}
          src={media.src}
          descriptions={media.descriptions}
          extended={media.extended}
          describedAudio={media.describedAudio}
          cueAudioBase={media.cueAudioBase}
          cueAudioExt={media.cueAudioExt}
          label={label}
        />
      </div>
      <div className={styles.edit}>{edit}</div>
      <CueScript className={styles.scriptArea} cues={cues} active={active} onSeek={seek} />
      <div className={styles.checks}>{checks}</div>
      <div className={styles.files}>{files}</div>
    </div>
  );
}
