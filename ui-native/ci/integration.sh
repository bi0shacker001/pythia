#!/usr/bin/env bash
# The native front end against Pythia's real Backend, served headless by serve_backend.py (the Python environment with
# pythia and PySide6 is active). Usage: integration.sh BINARY [--window]
#   1. no Pythia ever ran here: --probe says why and exits 2
#   2. the real Backend: --probe gets hello and get_system_info ok; with the Backend gone it says so and exits 2
#   3. reconnect: --watch sees ready, then the Backend killed (unavailable), then restarted (ready again, in time)
#   4. with --window: the real window under Xvfb, a screenshot, and the window's own log asserted
set -euo pipefail

BIN=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
WINDOW=${2:-}
chmod +x "$BIN"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TMP=${RUNNER_TEMP:-/tmp}
OUT=${OUT:-$PWD/integration-out}
mkdir -p "$OUT"
export PYTHONPATH="$ROOT"
server_pid=""

start_server() { # DATA_DIR LOG
  mkdir -p "$1"
  PYTHIA_DATA_DIR="$1" python "$ROOT/ui-native/ci/serve_backend.py" >"$2" 2>&1 &
  server_pid=$!
  for _ in $(seq 1 120); do
    grep -q '^ready' "$2" && return 0
    kill -0 "$server_pid" 2>/dev/null || break
    sleep 1
  done
  cat "$2"
  echo "the Backend did not come up"
  return 1
}

wait_for() { # FILE PATTERN SECONDS
  for _ in $(seq 1 "$3"); do
    grep -q "$2" "$1" && return 0
    sleep 1
  done
  echo "never saw '$2' in $1:"
  cat "$1"
  return 1
}

stop_server() {
  kill -9 "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
}
trap stop_server EXIT

echo "== 1. no Pythia has run here"
set +e
PYTHIA_DATA_DIR="$TMP/pythia-none" "$BIN" --probe | tee "$OUT/probe-none.txt"
status=${PIPESTATUS[0]}
set -e
test "$status" -eq 2
grep -q '^unavailable: Pythia has not run on this computer yet' "$OUT/probe-none.txt"

echo "== 2. the real Backend"
DATA="$TMP/pythia-data"
start_server "$DATA" "$OUT/server-probe.log"
PYTHIA_DATA_DIR="$DATA" "$BIN" --probe | tee "$OUT/probe.txt"
grep '^hello: ' "$OUT/probe.txt" | grep -q '"status":"ok"'
grep '^hello: ' "$OUT/probe.txt" | grep -q '"server":"pythia"'
grep '^get_system_info: ' "$OUT/probe.txt" | grep -q '"status":"ok"'
grep '^get_system_info: ' "$OUT/probe.txt" | grep -q '"system_uuid"'
stop_server
set +e
PYTHIA_DATA_DIR="$DATA" "$BIN" --probe | tee "$OUT/probe-gone.txt"
status=${PIPESTATUS[0]}
set -e
test "$status" -eq 2
grep -q '^unavailable: Pythia is not running' "$OUT/probe-gone.txt"

echo "== 3. reconnect: the Backend is killed and started again while the client runs"
DATA="$TMP/pythia-reconnect"
start_server "$DATA" "$OUT/server-reconnect-1.log"
PYTHIA_DATA_DIR="$DATA" "$BIN" --watch >"$OUT/watch.log" 2>&1 &
watch_pid=$!
wait_for "$OUT/watch.log" '^state: ready' 60
kill -9 "$server_pid"
wait "$server_pid" 2>/dev/null || true
wait_for "$OUT/watch.log" '^state: unavailable' 30
start_server "$DATA" "$OUT/server-reconnect-2.log"
restarted=$SECONDS
for _ in $(seq 1 40); do
  kill -0 "$watch_pid" 2>/dev/null || break
  sleep 1
done
kill -0 "$watch_pid" 2>/dev/null && {
  echo "the client did not come back"
  cat "$OUT/watch.log"
  kill -9 "$watch_pid"
  exit 1
}
wait "$watch_pid"
echo "the client was back $((SECONDS - restarted)) seconds after the Backend said ready"
test $((SECONDS - restarted)) -le 30
cat "$OUT/watch.log"
stop_server

if [ "$WINDOW" = "--window" ]; then
  echo "== 4. the window, under Xvfb, against the live Backend"
  DATA="$TMP/pythia-window"
  PYTHIA_SERVE_EMIT=gamesChanged start_server "$DATA" "$OUT/server-window.log"
  export PYTHIA_DATA_DIR="$DATA" LIBGL_ALWAYS_SOFTWARE=1 SDL_VIDEODRIVER=x11
  export OUT BIN
  xvfb-run -a -s "-screen 0 1280x800x24" bash -c '
    "$BIN" --smoke >"$OUT/ui.log" 2>&1 &
    app=$!
    for _ in $(seq 1 150); do
      grep -q "^smoke: " "$OUT/ui.log" && break
      kill -0 "$app" 2>/dev/null || break
      sleep 1
    done
    import -window root "$OUT/screen.png" || true
    set +e
    wait "$app"
    echo "exit=$?" >"$OUT/ui.exit"
  '
  cat "$OUT/ui.log"
  cat "$OUT/ui.exit"
  grep -q '^ui: rendered hello status=ok' "$OUT/ui.log"
  grep -q '^ui: rendered get_system_info status=ok' "$OUT/ui.log"
  grep -q '^ui: rendered events=[1-9]' "$OUT/ui.log"
  grep -q '^smoke: ok' "$OUT/ui.log"
  # The patched compose-native ends the window cleanly (the stock one raised BadWindow on teardown, exit 1).
  grep -q '^exit=0' "$OUT/ui.exit"
  test -s "$OUT/screen.png"
fi
echo "integration: ok"
