#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs
EXTERNAL_INTF="${1:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVER_AGENT_ROUTE_MODE="${SERVER_AGENT_ROUTE_MODE:-hybrid}"

if [ -n "${PATH_SERVICE_PYTHON:-}" ]; then
  :
elif [ -x "$HOME/miniconda3/envs/ryu_drl_s/bin/python" ]; then
  PATH_SERVICE_PYTHON="$HOME/miniconda3/envs/ryu_drl_s/bin/python"
else
  PATH_SERVICE_PYTHON="$PYTHON_BIN"
fi

cleanup() {
  ./stop_suite.sh >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [ -f logs/path_service.pid ] || [ -f logs/server_agent.pid ] || [ -f logs/start_controllers.pid ]; then
  echo "Existing pid files found under logs/. Run ./stop_suite.sh first if the suite is already running."
fi

"$PATH_SERVICE_PYTHON" drl-or-s/path_service.py --topo Military --port 8889 --model model/Military_mininet > logs/path_service.log 2>&1 &
echo $! > logs/path_service.pid

sleep 1

"$PYTHON_BIN" server_agent.py "$SERVER_AGENT_ROUTE_MODE" > logs/server_agent.stdout.log 2>&1 &
echo $! > logs/server_agent.pid

sleep 1

if [ -n "$EXTERNAL_INTF" ]; then
  export EXTERNAL_LINK_PORTS="${EXTERNAL_LINK_PORTS:-1:20}"
  echo "Hybrid physical attachment enabled: $EXTERNAL_INTF -> s1:port20"
  echo "Controller external link whitelist: $EXTERNAL_LINK_PORTS"
fi

"$PYTHON_BIN" -u start_controllers_test.py start -n > logs/controllers.log 2>&1 &
echo $! > logs/start_controllers.pid

echo "DRL-OR-S Routing Suite started"
echo "server socket: 6001"
echo "Web UI: http://localhost:6009"
echo "DRL path_service: 127.0.0.1:8889"
echo "server_agent route mode: $SERVER_AGENT_ROUTE_MODE"
echo "Starting Military Mininet topology in this terminal..."
echo "Exit the Mininet CLI to stop the suite."

if [ -n "$EXTERNAL_INTF" ]; then
  sudo "$PYTHON_BIN" testbed/creat_test_topo.py "$EXTERNAL_INTF" 2>&1 | tee logs/mininet_topology.log
else
  sudo "$PYTHON_BIN" testbed/creat_test_topo.py 2>&1 | tee logs/mininet_topology.log
fi
