#!/usr/bin/env bash
# One container, two processes: NeMo-Speech.cpp `serve` on 127.0.0.1:8089 (CPU, models baked
# into the image) and the Lull API (uvicorn, one worker) on $PORT. The ledger is restored from
# and replicated to Cloud Storage by Litestream when LULL_BUCKET is set.
#
# This script stays as PID 1: SIGTERM/SIGINT stop the API first (it drains for a few seconds),
# then the speech server; when either process ends on its own, the other is stopped and the
# container exits, so the platform restarts it.
set -euo pipefail

SPEECH_URL="http://127.0.0.1:8089"
SPEECH_WAIT="${LULL_SPEECH_WAIT:-120}"

log() { printf 'entrypoint: %s\n' "$*" >&2; }

export LULL_DATA="${LULL_DATA:-/data}"
export LULL_HOME="${LULL_HOME:-$LULL_DATA/home}"
export LULL_SPEECH_URL="$SPEECH_URL"
mkdir -p "$LULL_DATA" "$LULL_HOME"

# the speech server is private to this container: a fresh key per start unless one is given
if [[ -z "${LULL_SPEECH_KEY:-}" ]]; then
  LULL_SPEECH_KEY="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
fi
export LULL_SPEECH_KEY

if [[ -n "${LULL_BUCKET:-}" && -z "${LULL_LEDGER_REPLICA:-}" ]]; then
  LULL_LEDGER_REPLICA="gs://${LULL_BUCKET}/ledger"
fi
export LULL_LEDGER_REPLICA="${LULL_LEDGER_REPLICA:-}"

speech_pid=""
app_pid=""

stop() {
  trap - TERM INT
  if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
    kill -TERM "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
  fi
  if [[ -n "$speech_pid" ]] && kill -0 "$speech_pid" 2>/dev/null; then
    kill -TERM "$speech_pid" 2>/dev/null || true
    wait "$speech_pid" 2>/dev/null || true
  fi
}
trap 'log "stopping"; stop; exit 143' TERM
trap 'log "stopping"; stop; exit 130' INT

# -- the ledger --------------------------------------------------------------------------------
if [[ -n "$LULL_LEDGER_REPLICA" ]]; then
  log "restoring the ledger from its replica, if there is one and no local copy"
  litestream restore -if-db-not-exists -if-replica-exists \
    -o "$LULL_HOME/ledger.db" "$LULL_LEDGER_REPLICA"
else
  log "LULL_BUCKET is not set: the ledger is not replicated"
fi

# -- the speech server -------------------------------------------------------------------------
# Parakeet TDT, Nemotron-3-Diarization and Magpie (+ NanoCodec, tokenizer), pulled at build
# time; MODELS links each to its file. The key goes in through the environment, never on the
# command line (where ps shows it). Magpie gets one thread per CPU (its default, 4, would
# oversubscribe a 2-vCPU instance).
MODELS="${NEMO_SPEECH_MODEL_DIR:-/opt/nemo-models}/serve"
SPEECH_THREADS="${LULL_SPEECH_THREADS:-$(nproc)}"
log "starting the speech server (CPU, ${SPEECH_THREADS} TTS threads)"
NEMO_SPEECH_HTTP_API_KEY="$LULL_SPEECH_KEY" nemo-speech serve \
  --host 127.0.0.1 --port 8089 --device cpu --no-ui \
  --asr-model "$MODELS/asr.gguf" --diar-model "$MODELS/diar.gguf" \
  --tts-model "$MODELS/tts.gguf" --codec-model "$MODELS/codec.gguf" \
  --tokenizer-dir "$MODELS/tokenizer" --tts.threads "$SPEECH_THREADS" &
speech_pid=$!

started=$SECONDS
until nemo-speech --quiet health --url "$SPEECH_URL/health" >/dev/null 2>&1; do
  if ! kill -0 "$speech_pid" 2>/dev/null; then
    rc=0
    wait "$speech_pid" || rc=$?
    log "the speech server exited (status $rc) before it was ready"
    exit 1
  fi
  if (( SECONDS - started >= SPEECH_WAIT )); then
    log "the speech server was not ready after ${SPEECH_WAIT}s"
    stop
    exit 1
  fi
  sleep 0.5
done
log "the speech server is ready after $(( SECONDS - started ))s"

# -- the API -----------------------------------------------------------------------------------
# one worker: the live executor, the upload caps and the rate limits live in this process
app=(uvicorn lull.api.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
  --proxy-headers '--forwarded-allow-ips=*' --timeout-graceful-shutdown 8)
if [[ -n "$LULL_LEDGER_REPLICA" ]]; then
  log "starting the API under litestream (replicating the ledger)"
  litestream replicate -config /etc/litestream.yml -exec "${app[*]}" &
else
  log "starting the API"
  "${app[@]}" &
fi
app_pid=$!

# whichever ends first ends the container
rc=0
wait -n -p ended "$app_pid" "$speech_pid" || rc=$?
if [[ "${ended:-}" == "$speech_pid" ]]; then
  log "the speech server stopped (status $rc); stopping the API"
  rc=1
else
  log "the API stopped (status $rc)"
fi
stop
exit "$rc"
