#!/usr/bin/env bash
# One container, two processes: NeMo-Speech.cpp `serve` on 127.0.0.1:8089 (CPU, models baked
# into the image) and the Lull API (uvicorn, one worker) on $PORT. The ledger is restored from
# and replicated to Cloud Storage by Litestream when LULL_BUCKET is set.
#
# This script stays as PID 1. On SIGTERM/SIGINT it stops the API, then the speech server, each
# with a bound and SIGKILL after it, so the whole stop fits in Cloud Run's 10 s grace period:
#   API      <= 7.5 s  (uvicorn drains 5 s, then Litestream's final sync gets at most 2 s)
#   speech   <= 1.5 s
# When either process ends on its own, the other is stopped and the container exits, so the
# platform restarts it.
set -euo pipefail

SPEECH_URL="http://127.0.0.1:8089"
SPEECH_WAIT="${LULL_SPEECH_WAIT:-120}"
APP_STOP_DS=75     # tenths of a second the API gets to stop
SPEECH_STOP_DS=15  # tenths of a second the speech server gets to stop

log() { printf 'entrypoint: %s\n' "$*" >&2; }

# CPUs this container may use: the cgroup quota (cpu.max, or v1 cfs) when it is below nproc
cpus() {
  local n quota period
  n="$(nproc)"
  if read -r quota period < /sys/fs/cgroup/cpu.max 2>/dev/null; then
    :
  elif read -r quota < /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null \
      && read -r period < /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null; then
    :
  fi
  if [[ "${quota:-}" =~ ^[0-9]+$ && "${period:-}" =~ ^[1-9][0-9]*$ ]]; then
    quota=$(( (quota + period - 1) / period ))
    if (( quota >= 1 && quota < n )); then
      n=$quota
    fi
  fi
  echo "$n"
}

# LULL_NO_VERIFY is the engine's evaluation-only switch (no frame verification): never in a
# deployment. A true value stops the container here; any value is never passed on.
case "$(printf '%s' "${LULL_NO_VERIFY:-}" | tr '[:upper:]' '[:lower:]')" in
  ""|0|false|no|off) ;;
  *) log "LULL_NO_VERIFY is set: frame verification would be off; refusing to start"; exit 1 ;;
esac
unset LULL_NO_VERIFY

export LULL_DATA="${LULL_DATA:-/data}"
# the ledger (SQLite, WAL) on local disk, never the Cloud Storage mount; Litestream keeps the
# durable copy in the bucket
export LULL_HOME="${LULL_HOME:-/tmp/lull-home}"
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

# end PID: SIGTERM, wait at most LIMIT tenths of a second, then SIGKILL
end() {
  local pid=$1 limit=$2 name=$3 i
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill -TERM "$pid" 2>/dev/null || true
  for (( i = 0; i < limit; i++ )); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    log "the $name did not stop in $(( limit / 10 )).$(( limit % 10 ))s; killing it"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
}

stop() {
  trap - TERM INT
  end "$app_pid" "$APP_STOP_DS" API
  end "$speech_pid" "$SPEECH_STOP_DS" "speech server"
}
trap 'log "stopping"; stop; exit 143' TERM
trap 'log "stopping"; stop; exit 130' INT

# HTTP status of GET URL from the speech server, with its key when AUTH is "key"; 0 if unreachable
status() {
  timeout 5 python -I -c '
import os, sys, urllib.error, urllib.request
req = urllib.request.Request(sys.argv[1])
if sys.argv[2] == "key":
    req.add_header("Authorization", "Bearer " + os.environ["LULL_SPEECH_KEY"])
try:
    with urllib.request.urlopen(req, timeout=4) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print(0)
' "$1" "$2" || echo 0
}

# -- the ledger --------------------------------------------------------------------------------
# A failed restore (no access to the bucket, a broken replica) is fatal: starting with an empty
# ledger would reset the spend caps.
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
SPEECH_THREADS="${LULL_SPEECH_THREADS:-$(cpus)}"
log "starting the speech server (CPU, ${SPEECH_THREADS} TTS threads)"
NEMO_SPEECH_HTTP_API_KEY="$LULL_SPEECH_KEY" nemo-speech serve \
  --host 127.0.0.1 --port 8089 --device cpu --no-ui \
  --asr-model "$MODELS/asr.gguf" --diar-model "$MODELS/diar.gguf" \
  --tts-model "$MODELS/tts.gguf" --codec-model "$MODELS/codec.gguf" \
  --tokenizer-dir "$MODELS/tokenizer" --tts.threads "$SPEECH_THREADS" &
speech_pid=$!

started=$SECONDS
until timeout 5 nemo-speech --quiet health --url "$SPEECH_URL/health" >/dev/null 2>&1; do
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

# the key must be enforced, and must be the one the API holds
anon="$(status "$SPEECH_URL/v1/models" none)"
keyed="$(status "$SPEECH_URL/v1/models" key)"
if [[ "$anon" != 401 || "$keyed" != 200 ]]; then
  log "the speech server does not enforce its key (no key: HTTP $anon, key: HTTP $keyed)"
  stop
  exit 1
fi
log "the speech server is ready after $(( SECONDS - started ))s (no key: HTTP 401, key: HTTP 200)"

# -- the API -----------------------------------------------------------------------------------
# One worker: the live executor, the upload caps and the rate limits live in this process.
# No proxy-header handling in uvicorn: the app takes the visitor's address from the socket, or
# from X-Forwarded-For counted from the right when LULL_PROXY_HOPS says how many proxies to trust.
app=(uvicorn lull.api.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
  --no-proxy-headers --timeout-graceful-shutdown 5)
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
