# Lull web

The site for Lull: the landing page, Try it (a live run, streamed), the recorded runs and their
conformance reports, the studio, the developer docs, the benchmarks page, and the embeddable
`<lull-player>` (`player/`, built to `public/player.js`).

Next.js 15 (App Router), React 19, TypeScript, CSS Modules. pnpm 9, Node 20.9 or newer.

## Run it

```sh
pnpm install
pnpm dev            # builds the player, then http://localhost:3000
```

In development the site calls a Lull API at `http://localhost:8000` (the API's own `uvicorn`
default) unless `NEXT_PUBLIC_API_URL` says otherwise. The recorded runs, the docs and the landing
work without an API. Try it and the studio need one.

## Build

```sh
NEXT_PUBLIC_API_URL=https://api.example.org NEXT_PUBLIC_SITE_URL=https://lull.example pnpm build
pnpm start
```

A production build stops, and says why, when:

- `NEXT_PUBLIC_API_URL` is unset, or is not https (only a local API may be http);
- the site's own address is unknown: neither `NEXT_PUBLIC_SITE_URL` nor `VERCEL_PROJECT_PRODUCTION_URL` is set;
- a recorded run in `public/showcase/` is an interim placeholder rather than an engine export;
- the benchmarks file is the test fixture.

## Environment

| Variable | When | What |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | build (required in production) | The Lull API's address. It is inlined into the pages and named in the CSP. |
| `NEXT_PUBLIC_SITE_URL` | build (required in production unless on Vercel) | This site's public address, for sharing cards, the sitemap and embed snippets. |
| `VERCEL_PROJECT_PRODUCTION_URL` | build (set by Vercel) | The production domain, used as the site's address when `NEXT_PUBLIC_SITE_URL` is unset. |
| `LULL_BENCHMARKS_FILE` | build (optional) | The results file the benchmarks page is built from. Defaults to `public/benchmarks.json`. |
| `LULL_KITCHEN=1` | build (optional) | Serves `/kitchen`, the design-token page, from a production build. It is always on in development. |
| `LULL_API_SRC` | tests (optional) | A lull-api checkout's `engine/src/lull/api`, for the API-codes contract check. Defaults to a sibling checkout. |
| `E2E_PORT`, `E2E_BASE_URL`, `MOCK_API_PORT`, `E2E_CHROMIUM_PATH` | e2e (optional) | The site's port (3217), its address, the scripted API's port (3218), and the browser to use. |

## Content

- **Recorded runs:** `node scripts/sync-showcase.mjs <export dir> <id>` copies an engine export
  (`lull export-showcase RUN_DIR OUT_DIR --title … --credit …`) into `public/showcase/<id>/`. It
  checks the export first and rewrites the index. A `video.mp4` over 15 MiB is re-encoded on the
  way in with ffmpeg (at most 480 lines, H.264 CRF 30, audio copied unchanged so the timing holds);
  ffmpeg must be on the PATH for that. Keep `public/showcase/` under about 40 MB in all.
- **Benchmarks:** the evaluation's scorer writes `benchmarks.json`. Put it at
  `public/benchmarks.json` and rebuild. With no file, the page shows its empty state.
- **API refusal codes:** after the API changes its refusals, run `node scripts/api-codes.mjs` to
  refresh `test/fixtures/api-codes.json`. The reference and the client are checked against it.

## Test

```sh
pnpm lint && pnpm typecheck && pnpm test   # ESLint, tsc, Vitest
pnpm build:e2e                             # a production build for the e2e tests (see below)
pnpm test:e2e --workers=3                  # Playwright, with axe checks
pnpm test:screens                          # screenshots into e2e/__screens__/
```

`build:e2e` is the production build with three changes:

- it points at `http://localhost:8000`, which the tests route to a scripted API (`e2e/mock-api.mjs`);
- it is built from the benchmarks test fixture;
- it serves `/kitchen`.

`pnpm fixtures` regenerates the player's test media (it needs ffmpeg).

## Deploy (Vercel)

1. Set the project's root directory to `web/`. Keep the framework preset, Next.js.
2. Set the environment for both Production and Preview:
   - `NEXT_PUBLIC_API_URL`: the API's https address;
   - `NEXT_PUBLIC_SITE_URL`: this site's address. On Vercel it may be left unset, because Vercel sets
     `VERCEL_PROJECT_PRODUCTION_URL` itself; set it anyway to use a custom domain.
3. On the API, list this site's origin in `LULL_WEB_ORIGINS`. Preview deployments have their own
   addresses, so allow them as well, or point previews at a staging API.
4. After deploying, check four things:
   - `/robots.txt` and `/sitemap.xml`;
   - a link's sharing card;
   - `/player.js` and `/showcase/<id>/descriptions.vtt` answer with `Access-Control-Allow-Origin: *`;
   - the embed snippet on `/developers` plays when pasted into another site.
