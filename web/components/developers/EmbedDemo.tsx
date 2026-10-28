"use client";

import { Player } from "@/components/player/Player";
import { CopyCode } from "@/components/ui/CopyCode";
import { useSiteOrigin } from "@/components/ui/SiteOrigin";
import styles from "./Developers.module.css";

/**
 * The snippet for embedding the recorded run, with this site's address in it: it works pasted
 * onto any site (the player and the recorded runs' files are served to any origin), and
 * crossorigin lets the player dip the sound under each spoken line.
 */
export function embedSnippet(origin: string): string {
  const at = (p: string) => `${origin}${p}`;
  return `<script type="module" src="${at("/player.js")}"></script>

<lull-player
  src="${at("/showcase/ed90/video.mp4")}"
  poster="${at("/showcase/ed90/poster.jpg")}"
  descriptions="${at("/showcase/ed90/descriptions.vtt")}"
  described-audio="${at("/showcase/ed90/described-audio.m4a")}"
  extended="${at("/showcase/ed90/extended.vtt")}"
  cue-audio-base="${at("/showcase/ed90/audio/")}"
  cue-audio-ext="m4a"
  crossorigin="anonymous"
  mode="mixed"
  label="Elephants Dream, described">
</lull-player>`;
}

/**
 * The embed, twice: the snippet to paste (with this site's address once the page knows it),
 * and the same element playing the recorded run right here.
 */
export function EmbedDemo() {
  // the build's address when it knows one, then the page's own
  const origin = useSiteOrigin();

  return (
    <div className={styles.embed}>
      <CopyCode code={embedSnippet(origin)} label="Embed snippet" lang="html" />
      <figure className={styles.live}>
        <Player
          src="/showcase/ed90/video.mp4"
          poster="/showcase/ed90/poster.jpg"
          descriptions="/showcase/ed90/descriptions.vtt"
          describedAudio="/showcase/ed90/described-audio.m4a"
          extended="/showcase/ed90/extended.vtt"
          cueAudioBase="/showcase/ed90/audio/"
          cueAudioExt="m4a"
          mode="mixed"
          label="Elephants Dream, described"
        />
        <figcaption className={styles.liveCap}>The snippet above, live: the recorded run of Elephants Dream.</figcaption>
      </figure>
    </div>
  );
}
