import { existsSync } from "node:fs";
import { readdir } from "node:fs/promises";
import path from "node:path";
import { loadShowcase } from "./showcase";

/** A recorded run this site ships (public/showcase/<id>/): plays without the live API. */
export type Recorded = { id: string; title: string; credit: string; duration: number };

/** Every shipped recorded run, by id. Read at build time; a malformed showcase.json fails the build. */
export async function listRecorded(): Promise<Recorded[]> {
  const root = path.join(process.cwd(), "public", "showcase");
  let ids: string[];
  try {
    ids = (await readdir(root, { withFileTypes: true }))
      .filter((d) => d.isDirectory() && existsSync(path.join(root, d.name, "showcase.json")))
      .map((d) => d.name)
      .sort();
  } catch {
    return [];
  }
  const shows = await Promise.all(ids.map((id) => loadShowcase(id)));
  return shows.map(({ id, title, credit, duration }) => ({ id, title, credit, duration }));
}
