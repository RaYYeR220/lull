# The Lull API container

One linux/amd64 image for Google Cloud Run:

- the Lull API (FastAPI under uvicorn, one worker) and the engine, from `uv.lock`;
- NVIDIA NeMo-Speech.cpp 0.2.0 (CPU release) serving Parakeet TDT 0.6B v3 (ASR),
  Nemotron-3-Diarization and Magpie TTS 357M + NanoCodec on `127.0.0.1:8089`, with the
  models baked in so a cold start downloads nothing;
- ffmpeg/ffprobe 8.1 (BtbN static GPL build: libx264, aac, ebur128, loudnorm);
- Litestream 0.5.17, which restores and replicates the spend ledger (SQLite) to Cloud Storage.

## Build

From the repository root (the build context is `engine/`):

```bash
docker build --platform linux/amd64 -t lull-api:dev engine
```

A first build takes about 9 minutes on a home connection, most of it the 1.4 GB of models.
Every download is pinned and checked at build time:

| What | Version | From | SHA-256 |
|---|---|---|---|
| NeMo-Speech.cpp | 0.2.0 `linux-x86_64-cpu` | github.com/NVIDIA/NeMo-Speech.cpp release (also checked against its published `.sha256`) | `f396057150f1b774935c7414fd32ebc04195a795a34120d78d8c9ecefa1b7507` |
| ffmpeg | n8.1.3-9 `linux64-gpl` | github.com/BtbN/FFmpeg-Builds, tag `autobuild-2026-09-30-13-08` (month-end builds are kept for about two years) | `97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79` |
| Litestream | 0.5.17 | github.com/benbjohnson/litestream release | `cfb371176d164437ae869f8351cfde49bd1804ae71c61923f75c9cba9c9c006d` |
| python base | `python:3.11-slim-bookworm` | Docker Hub, pinned by index digest | `90d117a7f1ef6535dc75831e1a9184725757fede0d4d424d7977fb5429d50610` |
| uv | 0.11.23 | `ghcr.io/astral-sh/uv`, pinned by index digest | `d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479` |
| models | pinned by NeMo-Speech.cpp's index | Hugging Face, via `nemo-speech pull` (it checks each file's pinned size and SHA-256) | |

The build fails if ffmpeg reports a major version below 7 (the mix needs `-/filter_complex`).

The build links each model to `/opt/nemo-models/serve/` (`asr.gguf`, `diar.gguf`, `tts.gguf`,
`codec.gguf`, `tokenizer/`) and the server is given those paths. Indexed names
(`--asr-model parakeet-tdt`) would resolve to the same files but take a write lock inside the
cache, which stays root-owned and read-only for the app user.

The last build step imports the app as the user it runs as (`lull`, uid 10001), with
`LULL_DATA`/`LULL_HOME` pointed at a temporary folder, so the build leaves no root-owned files
in the app's data paths.

ffmpeg is a GPL build. Its source is FFmpeg commit `29e619e767` (the `n8.1.3-9` in its name)
and BtbN/FFmpeg-Builds at the same `autobuild-2026-09-30-13-08` tag, which pins every bundled
library; the licence text is in `/usr/local/share/licenses/ffmpeg/`. Running the image as a
service is not distribution; anyone who passes the image itself on must offer that source.

## Run locally

```bash
docker run --rm -p 8080:8080 -v "$PWD/lull-data:/data" \
  -e NEBIUS_API_KEY -e TAVILY_API_KEY \
  -e LULL_WEB_ORIGINS=http://localhost:3000 \
  lull-api:dev
curl localhost:8080/v1/health   # {"ok":true,"speech":true,...} once both processes are up
```

- Add `--cpuset-cpus 0-3 --memory 8g` to match the Cloud Run instance (4 vCPU, 8 GiB).
- `/data` must be writable by uid 10001 (Docker Desktop bind mounts are; on Linux,
  `chown 10001 lull-data`).
- Nothing listens until the speech server is ready: allow up to a minute.
- The ledger is in `/tmp/lull-home` inside the container: it starts empty each time unless
  `LULL_BUCKET` (or `LULL_LEDGER_REPLICA`, e.g. `file:///data/replica`) is set.
- `uv run python docker/smoke.py http://localhost:8080` (from `engine/`, ffmpeg on PATH) checks
  a running container end to end; see "Deploy smoke" below.

## What starts (`docker/entrypoint.sh`)

1. With `LULL_BUCKET` (or `LULL_LEDGER_REPLICA`) set: `litestream restore` of
   `$LULL_HOME/ledger.db`, only if there is a replica and no local copy. A failed restore (no
   access to the bucket, a broken replica) is fatal: the container exits rather than start with
   an empty ledger, which would reset the spend caps.
2. `nemo-speech serve --device cpu --no-ui` on `127.0.0.1:8089`, its API key passed through the
   environment (never on the command line). Without `LULL_SPEECH_KEY` a random key is made per
   start; the API reads the same variable.
3. Wait up to `LULL_SPEECH_WAIT` seconds for the speech server's `/health` (each probe bounded
   to 5 s); if it exits or is not ready in time the container exits non-zero (the platform
   restarts it).
4. Check the key is enforced: `GET /v1/models` without it must answer 401 and with it 200,
   or the container exits non-zero.
5. `uvicorn lull.api.app:app --workers 1 --no-proxy-headers --timeout-graceful-shutdown 5` on
   `$PORT`, under `litestream replicate -exec` when the ledger is replicated. uvicorn does not
   read forwarding headers: the app takes the visitor's address from the socket, or from
   `X-Forwarded-For` counted from the right when `LULL_PROXY_HOPS` says how many proxies to
   trust.

The script stays as PID 1. If either process ends on its own, the other is stopped and the
container exits.

### Shutdown budget (Cloud Run sends SIGKILL 10 s after SIGTERM)

| Step | Bound |
|---|---|
| API: uvicorn drains open requests (SSE streams included), then exits | 5 s (`--timeout-graceful-shutdown 5`) |
| Litestream: final sync of the ledger after uvicorn exits | 2 s (`shutdown-sync-timeout` in `litestream.yml`) |
| the entrypoint gives the API (uvicorn + Litestream) in all | 7.5 s, then SIGKILL |
| then the speech server | 1.5 s, then SIGKILL |
| **worst case** | **9 s** |

Measured: `docker stop` takes about 1.2 s normally, and 8.5 s with the API frozen (`kill -STOP`),
where the entrypoint logs "the API did not stop in 7.5s; killing it".

With a live run in flight the API cannot exit within those 5 s: the engine runs in a worker
thread, which cannot be interrupted, and Python waits for it before the process ends. The
entrypoint then kills the API at 7.5 s and Litestream's final sync does not run, so the ledger
rows of the last sync interval (1 s) can be lost; the run itself reads `stopped` afterwards and
its owner can restart it from the stage cache.

## Environment

| Variable | Default in the image | Meaning |
|---|---|---|
| `PORT` | `8080` | API port (Cloud Run sets it) |
| `NEBIUS_API_KEY` | none | Token Factory key; live runs need it |
| `TAVILY_API_KEY` | none | Tavily key for the research stage |
| `LULL_WEB_ORIGINS` | none | comma-separated origins allowed by CORS |
| `LULL_PROXY_HOPS` | `0` | trusted proxies that append to `X-Forwarded-For`; 0 = the socket peer. Cloud Run: set `1` |
| `LULL_DATA` | `/data` | runs, showcase, samples |
| `LULL_HOME` | `/tmp/lull-home` | `ledger.db` and `voices.json`, on local disk: SQLite must never sit on the Cloud Storage mount. Litestream keeps the durable copy in the bucket |
| `LULL_BUCKET` | none | Cloud Storage bucket for the ledger replica (`gs://$LULL_BUCKET/ledger`); unset = no replication |
| `LULL_LEDGER_REPLICA` | `gs://$LULL_BUCKET/ledger` | any Litestream replica URL, e.g. `file:///data/replica` to try replication locally |
| `LULL_SPEECH_KEY` | random per start | bearer key between the API and the speech server |
| `LULL_SPEECH_WAIT` | `120` | seconds to wait for the speech server at start |
| `LULL_SPEECH_THREADS` | the cgroup CPU quota (`cpu.max`), else `nproc` | Magpie TTS CPU threads |
| `LULL_CAP_RUN` / `LULL_CAP_DAY` / `LULL_CAP_TOTAL` | `0.50` / `3.00` / `35.00` | spend caps in USD on model calls only; `none` = no cap, `0` refuses every call |
| `LULL_TAVILY_DAY` | `60` | web searches per day (counted, never priced); a live run may make 3 |
| `LULL_SAMPLES` | `/opt/lull/samples` | the sample clips and `samples.json` baked into the image (read-only) |
| `LULL_RUN_WALL_CLOCK_S` | `900` | how long a live run may take before it is given up (error event, error.json) |
| `LULL_UPLOAD_RETENTION_DAYS` | `7` | an upload's run is removed this many days after it was made (a fork: 24 h, and never after the upload it came from), by housekeeping at start, hourly, and from `/v1/health` when an hour has passed; `/v1/health` states it |
| `LULL_PUBLIC_URL` | none | required in production: the API's public address (e.g. `https://lull-api-xyz.a.run.app`), comma-separated for more than one (a smoke tag URL, a custom domain). The first makes the links MCP `get_tracks` hands out; all are the host names `/mcp` answers to. Unset, links are relative, `/mcp` answers localhost only, and the API warns at start |
| `LULL_SHOWCASE_EDITABLE` | `0` | local only: lets the Studio change showcase runs |
| `LULL_NO_VERIFY` | unset | the engine's evaluation-only switch (no frame verification): a true value stops the container at start, and the API refuses to start with it; it is never passed on |

Litestream authenticates to Cloud Storage with the runtime service account (Application
Default Credentials); it needs object read/write on the bucket.

## On Cloud Run

`docker/deploy-cloudrun.sh` does every step below, from `engine/` (it skips what exists, so it
reruns): `PROJECT=<project> REGION=us-central1 docker/deploy-cloudrun.sh`, with
`NEBIUS_API_KEY` and `TAVILY_API_KEY` in the environment the first time (they go to Secret
Manager from stdin as `lull-nebius-api-key` and `lull-tavily-api-key`, never to disk). It uses
the active gcloud account. From Git Bash on Windows it keeps `mount-path=/data` and the cron
line as written (`MSYS2_ARG_CONV_EXCL`); MSYS would otherwise turn them into Windows paths.

1. **Images:** an Artifact Registry repository `lull` (docker) whose cleanup policy keeps the
   2 newest images.
2. **Keys:** the two secrets.
3. **Data:** the bucket `<project>-lull-data`, created with hierarchical namespace, uniform
   access and no soft delete, and a lifecycle rule deleting anything under `runs/` 8 days
   after it was written, as a backstop to the app's own retention (uploads 7 days, forks a
   day). Publishing a run renames folders; on a hierarchical bucket a folder rename is one
   atomic operation, where a flat bucket mounted with Cloud Storage FUSE refuses it (the
   engine then falls back to moving the files one by one, which works but is not atomic).

   ```bash
   gcloud storage buckets create "gs://$BUCKET" --location "$REGION"      --uniform-bucket-level-access --enable-hierarchical-namespace --soft-delete-duration=0
   echo '{"rule": [{"action": {"type": "Delete"},
     "condition": {"age": 8, "matchesPrefix": ["runs/"]}}]}' > lifecycle.json
   gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=lifecycle.json
   ```

4. **Identity:** a service account `lull-api` with `roles/storage.objectUser` on the bucket and
   `roles/secretmanager.secretAccessor` on the two secrets, and nothing project-wide.
5. **Build:** `gcloud builds submit . --config docker/cloudbuild.yaml` (Cloud Build, with BuildKit,
   which the Dockerfile's `COPY --chmod` needs). The upload is what `.gcloudignore` lets
   through: the Dockerfile, `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `samples/`
   and the two files the image runs from `docker/` (60 files, 13 MB). No `.venv`, tests,
   runs, `.env` or keys.
6. **Deploy** `lull-api` with these flags:
   - size and scaling: gen2, `--cpu 8 --memory 8Gi --concurrency 20 --timeout 3600
     --min-instances 0 --max-instances 1 --cpu-throttling --cpu-boost`;
   - the bucket at `/data`, the secrets as `NEBIUS_API_KEY` / `TAVILY_API_KEY`;
   - `--allow-unauthenticated`, and the environment from a file (`--env-vars-file`), since
     the origins and URLs hold commas: `LULL_DATA=/data`, `LULL_HOME=/tmp/lull-home`, `LULL_BUCKET`,
     `LULL_PROXY_HOPS=1`, `LULL_SPEECH_THREADS=8`, `LULL_SPEECH_WAIT=200`,
     `LULL_RUN_WALL_CLOCK_S=900`, `LULL_WEB_ORIGINS`, `LULL_PUBLIC_URL`.

   The first deploy takes the traffic; the script then sets `LULL_PUBLIC_URL` to the service's
   two URLs and their `smoke---` tag URLs. Later deploys go out with `--no-traffic --tag
   smoke`.
7. **Keep-warm:** a Cloud Scheduler job `lull-keep-warm` sends `GET /v1/health` every 10
   minutes, 07:00-23:00 UTC (`*/10 7-22 * * *`).

Notes:

- **Request-based billing** (`--cpu-throttling`, not `--no-cpu-throttling`): the instance has
  CPU only while a request is in flight. A live run works inside its own streaming request.
  - **Leaving does not stop a run here.** Cloud Run does not pass a client's leaving on to the
    container: the platform keeps the request open until the response ends, so a run whose
    page closed still has its CPU and runs to the end. Measured: a client that left 13 s in
    held the request for 527 s, and the run finished `done`. Its owner reads it when it is
    done, or follows it again with the edit token.
  - Where a connection really drops (other hosts, local runs), the run stops and its owner
    restarts it from the stage cache.
  - Litestream syncs while requests run and at shutdown.
  - Housekeeping (retention) runs at start, hourly while the instance lives, and from
    `/v1/health` when an hour has passed, so the keep-warm ping keeps it going.
- **Speed:** a run is slower on Cloud Run's 4 vCPU than on a desktop, mostly in voicing. bbb45
  (45 s) took 514 s end to end (listen 33 s, watch 15 s, plan 7 s, finalize 427 s, publish
  29 s), against 182 s in Docker on a Ryzen 5 5600X. That is close to the 10-minute wall
  clock, so a 60 s upload with many lines may hit it.
- **Concurrency:** `--concurrency 20`. The work that loads the CPU is gated in the app (one
  live run, two uploads received at once, two Studio edits re-checked and one publish at a
  time), so the other slots are for reads, health and the event streams (at most 3 follow
  one run and 8 are open in all).
  - The service has a single instance by design (below). So while a run keeps the CPU busy,
    the platform itself can answer a request 429 "no available instance" now and then: seen
    once while polling twice a second.
  - That 429 is the platform's, without the API's JSON or Retry-After. Clients retry it after
    a few seconds.
- **One instance:** `--max-instances 1`, because the rate limits, the live executor and the
  ledger live in one process, and Litestream allows one writer per replica. `max-instances`
  counts per revision, so later deploys go out with `--no-traffic --tag smoke`, and the
  traffic moves once the old revision's `/v1/health` says `"live_busy": false`.
- **Visitor address:** `LULL_PROXY_HOPS=1`, because Cloud Run's front end appends the visitor's
  address. Checked: requests carrying made-up `X-Forwarded-For` values still count against
  the sender's own limit.
- **Start:** `LULL_HOME` stays on local disk, and `LULL_BUCKET` keeps the ledger across
  restarts (checked: the spend survived a scale to zero). Measured with `--cpu-boost`:
  - an instance a deploy starts answers in about 37 s, of which the speech server takes 19 s;
  - a request that finds no instance waits about 130 s. The image streams in as it is read,
    so the speech server takes 45 s and the Python imports 71 s.

  The keep-warm job keeps an instance by day, and `LULL_SPEECH_WAIT=200` leaves room under
  the 240 s startup probe.
- **Lifecycle rule:** it deletes objects under `runs/` 8 days after they were written,
  whatever the app did. An upload's run is gone by then anyway, and a visitor's sample run
  that old goes with it. The showcase (`showcase/`) is not touched.

### Recorded runs (the showcase)

`GET /v1/runs` lists the folders under `showcase/` in the bucket (an empty list until there
are some). To add a finished run folder, for example `runs/ed90`:

```bash
gcloud storage cp -r runs/ed90 "gs://<project>-lull-data/showcase/"
```

The run appears in the list within 5 seconds. Its video is found among the baked-in samples
by name and hash, or in its own `input/`.

### Deploy smoke

Against the `smoke` tag's URL of the new revision (it has no traffic yet; on the first
deploy, the service URL), from `engine/` with ffmpeg on PATH:

```bash
uv run python docker/smoke.py "$SMOKE_URL" --platform-keeps-requests
uv run python docker/smoke.py "$SMOKE_URL" --mcp --mcp-list-only
```

The first command does five checks:

1. health, the samples and the run list;
2. a sample run (bbb45) left once its first stage starts. On Cloud Run it runs to `done`
   (`--platform-keeps-requests`); elsewhere it must read `stopped`;
3. for a stopped run, the owner restarts it to `done`, with a stage from the cache and model
   spend within $0.05;
4. a 2560x1440 upload, which must be refused with 422 unsupported;
5. over the real mount: fork the run, approve or hold one cue, and publish it.

`--run-id <a finished run>` skips the run (no spend). The second command checks `/mcp`:
`initialize` and `tools/list`. Plain `--mcp` also runs one `describe_video` to `done`, within
the spend bound. Neither prints a token.

## Sizes

| Part | On disk |
|---|---|
| models (`/opt/nemo-models`) | 1.4 GB |
| ffmpeg + ffprobe (static) | 344 MB |
| Python environment (`/app/.venv`, ortools and numpy are most of it) | 283 MB |
| python:3.11-slim-bookworm | 138 MB |
| Litestream | 39 MB |
| NeMo-Speech.cpp runtime | 17 MB |
| **whole image** | **2.2 GB on disk, 1.67 GB compressed** |

BtbN's `linux64-gpl-shared` build would save about 170 MB (one copy of the libraries for both
binaries) if the image ever needs to shrink.

## Speed (Docker on a Ryzen 5 5600X host, which was busy, so read these as rough)

4 vCPU, 8 GiB (`--cpuset-cpus 0-3 --memory 8g`), the Cloud Run size:

- Cold start: 55 s from `docker run` to a healthy API (52 s of it the speech server loading and
  warming its models); 19 s with the model files already in the page cache.
- A live 20 s run with two voiced cues, no Tavily: 41.7 s (listen 8.4 s, watch 4.8 s,
  plan 5.1 s, finalize 18.0 s, publish 3.5 s).

2 vCPU, 4 GiB (`--cpuset-cpus 0-1 --memory 4g`):

- Cold start, first run of a fresh image: 64 s from `docker run` to a healthy API, 58 s of it
  the speech server loading and warming its models. Later starts, with the model files in the
  page cache: 19-22 s.
- Speech, warm: ASR 4-6 s and diarization about 8 s for 20 s of audio; Magpie TTS runs at
  0.3-0.45x real time (a 4 s line takes 9-12 s). Voicing is the slow part of a live run.
- A live 20 s run end to end through `POST /v1/runs`: 27 s with no cues to voice
  (listen 11.8 s, watch 6.5 s, research 6.7 s, publish 1.5 s); 70 s with three voiced cues
  (listen 11.2 s, watch 5.9 s, research 10.1 s, plan 4.8 s, finalize 34.6 s, publish 2.8 s).
