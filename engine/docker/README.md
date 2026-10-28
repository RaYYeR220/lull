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
| uv | 0.11.23 | `ghcr.io/astral-sh/uv` image | (image tag) |
| models | pinned by NeMo-Speech.cpp's index | Hugging Face, via `nemo-speech pull` (it checks each file's pinned size and SHA-256) | |

The build fails if ffmpeg reports a major version below 7 (the mix needs `-/filter_complex`).

The build links each model to `/opt/nemo-models/serve/` (`asr.gguf`, `diar.gguf`, `tts.gguf`,
`codec.gguf`, `tokenizer/`) and the server is given those paths. Indexed names
(`--asr-model parakeet-tdt`) would resolve to the same files but take a write lock inside the
cache, which stays root-owned and read-only for the app user.

## Run locally

```bash
docker run --rm -p 8080:8080 -v "$PWD/lull-data:/data" \
  -e NEBIUS_API_KEY -e TAVILY_API_KEY \
  -e LULL_WEB_ORIGINS=http://localhost:3000 -e LULL_PROXY_HOPS=0 \
  lull-api:dev
curl localhost:8080/v1/health   # {"ok":true,"speech":true,...} once both processes are up
```

- Add `--cpuset-cpus 0-1 --memory 4g` to match the Cloud Run instance (2 vCPU, 4 GiB).
- `/data` must be writable by uid 10001 (Docker Desktop bind mounts are; on Linux,
  `chown 10001 lull-data`).
- Nothing listens until the speech server is ready: allow about a minute on 2 CPUs.

## What starts (`docker/entrypoint.sh`)

1. With `LULL_BUCKET` (or `LULL_LEDGER_REPLICA`) set: `litestream restore` of
   `$LULL_HOME/ledger.db`, only if there is a replica and no local copy.
2. `nemo-speech serve --device cpu --no-ui` on `127.0.0.1:8089`, its API key passed through the
   environment (never on the command line). Without `LULL_SPEECH_KEY` a random key is made per
   start; the API reads the same variable.
3. Wait up to `LULL_SPEECH_WAIT` seconds for the speech server's `/health`; if it exits or is
   not ready in time the container exits non-zero (the platform restarts it).
4. `uvicorn lull.api.app:app --workers 1 --proxy-headers --forwarded-allow-ips=*
   --timeout-graceful-shutdown 8` on `$PORT`, under `litestream replicate -exec` when the ledger
   is replicated.

The script stays as PID 1. SIGTERM/SIGINT stop the API first (in-flight requests get 8 s,
inside Cloud Run's 10 s), then the speech server. If either process ends on its own, the other
is stopped and the container exits.

## Environment

| Variable | Default in the image | Meaning |
|---|---|---|
| `PORT` | `8080` | API port (Cloud Run sets it) |
| `NEBIUS_API_KEY` | none | Token Factory key; live runs need it |
| `TAVILY_API_KEY` | none | Tavily key for the research stage |
| `LULL_WEB_ORIGINS` | none | comma-separated origins allowed by CORS |
| `LULL_PROXY_HOPS` | `1` | trusted proxies that append to `X-Forwarded-For` (Cloud Run: 1). Set `0` when clients connect directly, as in a local run |
| `LULL_DATA` | `/data` | runs, showcase, samples |
| `LULL_HOME` | `/data/home` | `ledger.db` and `voices.json`. SQLite needs a local disk: when `/data` is a Cloud Storage (FUSE) mount, point this at a local path such as `/home/lull/state` and let Litestream keep the durable copy |
| `LULL_BUCKET` | none | Cloud Storage bucket for the ledger replica (`gs://$LULL_BUCKET/ledger`); unset = no replication |
| `LULL_LEDGER_REPLICA` | `gs://$LULL_BUCKET/ledger` | any Litestream replica URL, e.g. `file:///data/replica` to try replication locally |
| `LULL_SPEECH_KEY` | random per start | bearer key between the API and the speech server |
| `LULL_SPEECH_WAIT` | `120` | seconds to wait for the speech server at start |
| `LULL_SPEECH_THREADS` | `nproc` | Magpie TTS CPU threads |
| `LULL_CAP_RUN` / `LULL_CAP_DAY` / `LULL_CAP_TOTAL` | `0.50` / `3.00` / `35.00` | spend caps in USD |
| `LULL_SHOWCASE_EDITABLE` | `0` | local only: lets the Studio change showcase runs |

Litestream authenticates to Cloud Storage with the runtime service account (Application
Default Credentials); it needs object read/write on the bucket.

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

## Speed on 2 CPUs (Docker, `--cpuset-cpus 0-1 --memory 4g`, Ryzen 5 5600X host)

- Cold start, first run of a fresh image: 64 s from `docker run` to a healthy API, 58 s of it
  the speech server loading and warming its models. Later starts, with the model files in the
  page cache: 19-22 s.
- Speech, warm: ASR 4-6 s and diarization about 8 s for 20 s of audio; Magpie TTS runs at
  0.3-0.45x real time (a 4 s line takes 9-12 s). Voicing is the slow part of a live run.
- A live 20 s run end to end through `POST /v1/runs`: 27 s with no cues to voice
  (listen 11.8 s, watch 6.5 s, research 6.7 s, publish 1.5 s); 70 s with three voiced cues
  (listen 11.2 s, watch 5.9 s, research 10.1 s, plan 4.8 s, finalize 34.6 s, publish 2.8 s).
