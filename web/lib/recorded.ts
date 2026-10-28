import { loadIndex } from "./showcase";
import { listNames } from "./showcase-derive";

/** A recorded run this site ships (public/showcase/<id>/): plays without the live API. */
export type Recorded = { id: string; title: string; credit: string; duration: number };

/** Every shipped recorded run, in the index's order. Read at build time; a stale or malformed index fails the build. */
export async function listRecorded(): Promise<Recorded[]> {
  const index = await loadIndex();
  const names = listNames(index);
  return index.map(({ id, title, credit, duration }) => ({ id, title: names[id] ?? title, credit, duration }));
}
