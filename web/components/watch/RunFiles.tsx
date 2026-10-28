import { stat } from "node:fs/promises";
import path from "node:path";
import { showcaseMedia, type Showcase } from "@/lib/showcase";
import styles from "./Watch.module.css";

type FileRow = { href: string; name: string; kind: string; what: string };

/** "2.2 MB", "812 bytes". */
function size(bytes: number): string {
  if (bytes < 1000) return `${bytes} bytes`;
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** The run's files this site serves, each with its size, read at build time. */
export async function RunFiles({ showcase }: { showcase: Showcase }) {
  const media = showcaseMedia(showcase);
  const rows: FileRow[] = [
    { href: media.descriptions, name: "descriptions.vtt", kind: "WebVTT", what: "Every voiced line with its timing, as a descriptions track." },
    { href: media.extended, name: "extended.vtt", kind: "WebVTT", what: "The extended lines, each with how long the picture holds." },
    ...(media.describedAudio
      ? [{ href: media.describedAudio, name: "described-audio.m4a", kind: "AAC audio", what: "The original sound with the lines mixed in, as its own track." }]
      : []),
    { href: media.video, name: "video.mp4", kind: "480p video", what: "The original clip and its sound, as this page plays it." },
  ];
  const sized = await Promise.all(
    rows.map(async (r) => {
      const file = path.join(process.cwd(), "public", ...r.href.split("/").filter(Boolean));
      return { ...r, bytes: (await stat(file)).size };
    }),
  );

  return (
    <section aria-labelledby="files-title" className={styles.block}>
      <div className={styles.blockHead}>
        <h2 id="files-title" className={styles.h2}>
          Files from this run
        </h2>
      </div>
      <ul className={styles.fileList}>
        {sized.map((f) => (
          <li key={f.name} className={styles.file}>
            <a href={f.href} className={styles.fileName} download>
              {f.name}
            </a>
            <span className={styles.fileMeta}>
              {f.kind}, {size(f.bytes)}
            </span>
            <span className={styles.fileWhat}>{f.what}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
