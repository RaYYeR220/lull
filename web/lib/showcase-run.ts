import { existsSync } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { fromShowcase, showcaseFiles, type RunData } from "./run-data";
import { loadShowcase } from "./showcase";

/** A site URL under public/ as a path on disk. */
const onDisk = (url: string) => path.join(process.cwd(), "public", ...url.split("/").filter(Boolean));

/**
 * A recorded run as the pages read it, at build time: its evidence frames checked on disk and
 * its files' sizes read, so nothing on the page points at a file the site doesn't ship.
 */
export const loadShowcaseRun = cache(async (id: string): Promise<RunData> => {
  const s = await loadShowcase(id);
  const files = showcaseFiles(s);
  const sizes: Record<string, number | null> = {};
  await Promise.all(
    files.map(async (f) => {
      sizes[f.href] = (await stat(onDisk(f.href))).size;
    }),
  );
  return fromShowcase(s, { hasFrame: (url) => existsSync(onDisk(url)), sizes });
});
