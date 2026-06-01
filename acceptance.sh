#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMMAND="${1:-}"
CONFIG="${ACCEPTANCE_CONFIG:-config/hybrid_acceptance.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MININET_PYTHON="${MININET_PYTHON:-python3}"
SERVER_AGENT_ROUTE_MODE="${SERVER_AGENT_ROUTE_MODE:-hybrid}"
PYTHON_BIN_DIR="$(dirname "$PYTHON_BIN")"
if [ -x "$PYTHON_BIN_DIR/ryu-manager" ]; then
  export PATH="$PYTHON_BIN_DIR:$PATH"
fi

if [ -n "${PATH_SERVICE_PYTHON:-}" ]; then
  :
elif [ -x "$HOME/miniconda3/envs/ryu_drl_s/bin/python" ]; then
  PATH_SERVICE_PYTHON="$HOME/miniconda3/envs/ryu_drl_s/bin/python"
else
  PATH_SERVICE_PYTHON="$PYTHON_BIN"
fi

usage() {
  echo "Usage: $0 {start|stop|health|report}"
  echo "Config: $CONFIG"
}

sudo_cmd() {
  if [ -n "${SUDO_PASSWORD:-}" ]; then
    printf '%s\n' "$SUDO_PASSWORD" | sudo -S -v
  fi
  sudo "$@"
}

load_acceptance_env() {
  eval "$("$PYTHON_BIN" tools/acceptance_config.py --config "$CONFIG" --shell-env)"
}

wait_for_port() {
  local host="$1"
  local port="$2"
  local timeout_seconds="${3:-60}"
  local start_time
  start_time="$(date +%s)"
  while true; do
    if "$PYTHON_BIN" - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=1):
        pass
except OSError:
    raise SystemExit(1)
PY
    then
      return 0
    fi
    if [ "$(( $(date +%s) - start_time ))" -ge "$timeout_seconds" ]; then
      echo "timeout waiting for $host:$port" >&2
      return 1
    fi
    sleep 1
  done
}

wait_for_mininet_routes() {
  local host_name="$1"
  local routes_csv="$2"
  local timeout_seconds="${3:-240}"
  local start_time
  start_time="$(date +%s)"
  while true; do
    local pid
    pid="$(ps -eo pid=,args= | awk -v pattern="bash --norc --noediting -is mininet:${host_name}" '{$1=$1; pid=$1; sub(/^[^ ]+[ ]+/, "", $0); if ($0 == pattern) {print pid; exit}}' || true)"
    if [ -n "$pid" ]; then
      local route_output
      route_output="$(sudo_cmd mnexec -a "$pid" ip route 2>/dev/null || true)"
      local all_present=1
      local old_ifs="$IFS"
      IFS=","
      for route in $routes_csv; do
        route="${route#"${route%%[![:space:]]*}"}"
        route="${route%"${route##*[![:space:]]}"}"
        if [ -n "$route" ] && ! printf '%s\n' "$route_output" | grep -q "$route"; then
          all_present=0
        fi
      done
      IFS="$old_ifs"
      if [ "$all_present" -eq 1 ]; then
        return 0
      fi
    fi
    if [ "$(( $(date +%s) - start_time ))" -ge "$timeout_seconds" ]; then
      echo "timeout waiting for Mininet host $host_name routes: $routes_csv" >&2
      return 1
    fi
    sleep 2
  done
}

ensure_external_interface() {
  if command -v ip >/dev/null 2>&1; then
    ip link show "$EXTERNAL_INTF" >/dev/null
  else
    echo "warning: ip command not found; skip external interface existence check"
  fi
}

write_pid() {
  local name="$1"
  local pid="$2"
  echo "$pid" > "logs/${name}.pid"
}

start_suite() {
  mkdir -p logs reports
  rm -f logs/*.log
  load_acceptance_env
  ensure_external_interface

  nohup setsid "$PATH_SERVICE_PYTHON" drl-or-s/path_service.py --topo Military --port 8889 --model model/Military_mininet > logs/path_service.log 2>&1 &
  write_pid path_service "$!"
  wait_for_port 127.0.0.1 8889 90

  nohup setsid "$PYTHON_BIN" server_agent.py "$SERVER_AGENT_ROUTE_MODE" > logs/server_agent.stdout.log 2>&1 &
  write_pid server_agent "$!"
  sleep 1

  nohup setsid "$PYTHON_BIN" -u start_controllers_test.py start -n > logs/controllers.log 2>&1 &
  write_pid start_controllers "$!"
  for port in $CONTROLLER_PORTS; do
    wait_for_port 127.0.0.1 "$port" 90
  done

  sudo_cmd -E "$MININET_PYTHON" -u testbed/creat_test_topo.py "$EXTERNAL_INTF" --hold > logs/mininet_topology.log 2>&1 &
  write_pid mininet_topology "$!"
  wait_for_mininet_routes "$VALIDATION_VIRTUAL_HOST_NAME" "$HYBRID_REAL_ROUTES" 240

  echo "DRL-OR-S acceptance environment started"
  echo "Web UI: http://localhost:6009"
  echo "Health: ./acceptance.sh health"
  echo "Report: ./acceptance.sh report"
}

stop_suite() {
  if [ -f start_controllers_test.py ]; then
    "$PYTHON_BIN" start_controllers_test.py stop || true
  fi

  if [ -d logs ]; then
    for pidfile in logs/*.pid; do
      [ -e "$pidfile" ] || continue
      pid="$(cat "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    done
  fi

  pkill -f "[s]erver_agent.py" 2>/dev/null || true
  pkill -f "[d]rl-or-s/path_service.py" 2>/dev/null || true
  pkill -f "[t]estbed/creat_test_topo.py" 2>/dev/null || true

  if command -v sudo >/dev/null 2>&1; then
    sudo_cmd mn -c || true
  else
    echo "warning: sudo command not found; skip sudo mn -c"
  fi

  echo "DRL-OR-S acceptance environment stopped"
}

case "$COMMAND" in
  start)
    start_suite
    ;;
  stop)
    stop_suite
    ;;
  health)
    "$PYTHON_BIN" tools/acceptance_health.py --config "$CONFIG"
    ;;
  report)
    "$PYTHON_BIN" tools/generate_acceptance_report.py --config "$CONFIG"
    ;;
  *)
    usage
    exit 2
    ;;
esac
