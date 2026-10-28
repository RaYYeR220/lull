# syntax=docker/dockerfile:1.7
# The Lull API for Cloud Run: FastAPI + the engine, NVIDIA NeMo-Speech.cpp on CPU (Parakeet ASR,
# Nemotron-3-Diarization, Magpie TTS, models baked in), static ffmpeg >= 7 and Litestream.
# Build from engine/: docker build --platform linux/amd64 -t lull-api:dev .
# Every download is checked against a SHA-256 pinned here; see docker/README.md.

# python:3.11-slim-bookworm and uv 0.11.23, pinned by digest (multi-arch index; amd64 is used)
ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:90d117a7f1ef6535dc75831e1a9184725757fede0d4d424d7977fb5429d50610

FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.11.23@sha256:d0a0a753ab981624b49c97abc98821c1c09f4ca69d1ef5cee69c501be3d88479 AS uv

# -- downloads ---------------------------------------------------------------------------------
FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS fetch
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /fetch

# ffmpeg + ffprobe: BtbN static GPL build (libx264, aac, ebur128, loudnorm). A month-end
# autobuild: BtbN keeps those for about two years, the daily ones only for a few weeks.
FROM fetch AS ffmpeg
ARG FFMPEG_RELEASE=autobuild-2026-09-30-13-08
ARG FFMPEG_NAME=ffmpeg-n8.1.3-9-g29e619e767-linux64-gpl-8.1
ARG FFMPEG_SHA256=97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79
RUN curl -fsSL -o ffmpeg.tar.xz \
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_RELEASE}/${FFMPEG_NAME}.tar.xz" \
    && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/ffmpeg/bin /opt/ffmpeg/share \
    && tar -xJf ffmpeg.tar.xz --no-same-owner --strip-components=2 -C /opt/ffmpeg/bin \
        "${FFMPEG_NAME}/bin/ffmpeg" "${FFMPEG_NAME}/bin/ffprobe" \
    && tar -xJf ffmpeg.tar.xz --no-same-owner --strip-components=1 -C /opt/ffmpeg/share \
        "${FFMPEG_NAME}/LICENSE.txt" \
    && rm ffmpeg.tar.xz \
    && major="$(/opt/ffmpeg/bin/ffmpeg -hide_banner -version \
        | sed -n 's/^ffmpeg version n\{0,1\}\([0-9][0-9]*\)\..*/\1/p')" \
    && echo "ffmpeg major version: ${major}" \
    && test "${major:-0}" -ge 7

# NeMo-Speech.cpp: the official CPU release (it carries its own libstdc++, libgomp and libgcc_s;
# glibc >= 2.31 is all it needs), checked against the pin and the published .sha256.
FROM fetch AS nemo
ARG NEMO_VERSION=0.2.0
ARG NEMO_SHA256=f396057150f1b774935c7414fd32ebc04195a795a34120d78d8c9ecefa1b7507
RUN name="nemo-speech-${NEMO_VERSION}-linux-x86_64-cpu.tar.gz" \
    && base="https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v${NEMO_VERSION}" \
    && curl -fsSLO "${base}/${name}" \
    && curl -fsSLO "${base}/${name}.sha256" \
    && echo "${NEMO_SHA256}  ${name}" | sha256sum -c - \
    && sha256sum -c "${name}.sha256" \
    && mkdir -p /opt/nemo-speech \
    && tar -xzf "${name}" --no-same-owner --strip-components=1 -C /opt/nemo-speech \
    && rm -rf "${name}" "${name}.sha256" \
        /opt/nemo-speech/include /opt/nemo-speech/lib/cmake /opt/nemo-speech/share/doc \
    && /opt/nemo-speech/bin/nemo-speech --version
# The models, so a cold start downloads nothing; pull checks each file's pinned size and SHA-256
# and prints "repo<TAB>role<TAB>path". serve/ links each role to its file: the server is given
# those paths, because looking an indexed name up takes a write lock inside the cache, which
# stays root-owned and read-only for the app user.
ENV NEMO_SPEECH_MODEL_DIR=/opt/nemo-models
RUN set -e \
    && for m in parakeet-tdt nemotron-3-diarization magpie; do \
        /opt/nemo-speech/bin/nemo-speech pull "$m" >> /fetch/pulled.tsv; \
    done \
    && mkdir /opt/nemo-models/serve \
    && for pair in asr:asr.gguf diarization:diar.gguf tts:tts.gguf codec:codec.gguf \
            tokenizer:tokenizer; do \
        role="${pair%%:*}"; \
        path="$(awk -F '\t' -v r="$role" '$2 == r { print $3 }' /fetch/pulled.tsv)"; \
        test -e "$path"; \
        ln -s "$path" "/opt/nemo-models/serve/${pair#*:}"; \
    done \
    && find /opt/nemo-models -name '*.lock' -delete \
    && chmod -R a+rX /opt/nemo-models \
    && ls -l /opt/nemo-models/serve

# Litestream: replicates the ledger (SQLite) to Cloud Storage.
FROM fetch AS litestream
ARG LITESTREAM_VERSION=0.5.17
ARG LITESTREAM_SHA256=cfb371176d164437ae869f8351cfde49bd1804ae71c61923f75c9cba9c9c006d
RUN curl -fsSL -o litestream.tar.gz \
        "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz" \
    && echo "${LITESTREAM_SHA256}  litestream.tar.gz" | sha256sum -c - \
    && tar -xzf litestream.tar.gz --no-same-owner -C /usr/local/bin litestream \
    && rm litestream.tar.gz \
    && litestream version

# -- the Python environment (uv, from the lockfile) --------------------------------------------
FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.11 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# the dependencies first: this layer only changes with the lockfile
RUN uv sync --frozen --no-dev --no-install-project
# the project itself (its pyproject names README.md as the readme)
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# -- the image ---------------------------------------------------------------------------------
FROM --platform=linux/amd64 ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 lull \
    && useradd --system --uid 10001 --gid lull --home-dir /home/lull --create-home \
        --shell /usr/sbin/nologin lull
# largest and least often changed first
COPY --from=nemo /opt/nemo-models /opt/nemo-models
COPY --from=nemo /opt/nemo-speech /opt/nemo-speech
COPY --from=ffmpeg /opt/ffmpeg/bin/ /usr/local/bin/
COPY --from=ffmpeg /opt/ffmpeg/share/LICENSE.txt /usr/local/share/licenses/ffmpeg/LICENSE.txt
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/lull-entrypoint
COPY docker/litestream.yml /etc/litestream.yml
# the licensed sample clips and their credits (samples.json), read-only
COPY samples /opt/lull/samples

ENV PATH=/app/.venv/bin:/opt/nemo-speech/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEMO_SPEECH_MODEL_DIR=/opt/nemo-models \
    LULL_DATA=/data \
    LULL_HOME=/tmp/lull-home \
    LULL_SPEECH_URL=http://127.0.0.1:8089 \
    LULL_PROXY_HOPS=0 \
    LULL_SAMPLES=/opt/lull/samples \
    PORT=8080

RUN python -m compileall -q /app/src \
    && mkdir -p /data /tmp/lull-home \
    && chown -R lull:lull /data /tmp/lull-home \
    && ffmpeg -hide_banner -version | head -n 1 \
    && nemo-speech --version \
    && litestream version

USER lull
WORKDIR /app
# the app imports and builds as the user it runs as, with its data paths out of the way
RUN check="$(mktemp -d)" \
    && LULL_DATA="$check" LULL_HOME="$check/home" python -c "import lull.api.app" \
    && rm -rf "$check"
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/lull-entrypoint"]
