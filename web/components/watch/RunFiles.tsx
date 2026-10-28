import type { RunFile } from "@/lib/run-data";
import styles from "./Watch.module.css";

/** "2.2 MB", "812 bytes". */
export function fileSize(bytes: number): string {
  if (bytes < 1000) return `${bytes} bytes`;
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** The run's files, each with what it is and its size (when the source knows it). */
export function RunFiles({ files, note }: { files: RunFile[]; /** A line under the heading, e.g. where the files are served from. */ note?: string }) {
  return (
    <section aria-labelledby="files-title" className={styles.block}>
      <div className={styles.blockHead}>
        <h2 id="files-title" className={styles.h2}>
          Files from this run
        </h2>
        {note ? <p className={styles.note}>{note}</p> : null}
      </div>
      <ul className={styles.fileList}>
        {files.map((f) => (
          <li key={f.name} className={styles.file}>
            <a href={f.href} className={styles.fileName} download>
              {f.name}
            </a>
            <span className={styles.fileMeta}>
              {f.kind}
              {f.bytes !== null ? `, ${fileSize(f.bytes)}` : ""}
            </span>
            <span className={styles.fileWhat}>{f.what}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
