#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMMAND="${1:-}"
CONFIG="${ACCEPTANCE_CONFIG:-config/hybrid_acceptance.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVER_AGENT_ROUTE_MODE="${SERVER_AGENT_ROUTE_MODE:-hybrid}"

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
    printf '%s\n' "$SUDO_PASSWORD" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

load_acceptance_env() {
  eval "$("$PYTHON_BIN" tools/acceptance_config.py --config "$CONFIG" --shell-env)"
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
  load_acceptance_env
  ensure_external_interface

  nohup setsid "$PATH_SERVICE_PYTHON" drl-or-s/path_service.py --topo Military --port 8889 --model model/Military_mininet > logs/path_service.log 2>&1 &
  write_pid path_service "$!"
  sleep 1

  nohup setsid "$PYTHON_BIN" server_agent.py "$SERVER_AGENT_ROUTE_MODE" > logs/server_agent.stdout.log 2>&1 &
  write_pid server_agent "$!"
  sleep 1

  nohup setsid "$PYTHON_BIN" -u start_controllers_test.py start -n > logs/controllers.log 2>&1 &
  write_pid start_controllers "$!"
  sleep 1

  (tail -f /dev/null | sudo_cmd -E "$PYTHON_BIN" -u testbed/creat_test_topo.py "$EXTERNAL_INTF") > logs/mininet_topology.log 2>&1 &
  write_pid mininet_topology "$!"

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
