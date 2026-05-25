#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs

cleanup() {
  ./stop_suite.sh >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [ -f logs/path_service.pid ] || [ -f logs/server_agent.pid ] || [ -f logs/start_controllers.pid ]; then
  echo "Existing pid files found under logs/. Run ./stop_suite.sh first if the suite is already running."
fi

python3 drl-or-s/path_service.py --topo Military --port 8889 --model model/Military_mininet > logs/path_service.log 2>&1 &
echo $! > logs/path_service.pid

sleep 1

python3 server_agent.py > logs/server_agent.log 2>&1 &
echo $! > logs/server_agent.pid

sleep 1

python3 -u start_controllers_test.py start -n > logs/controllers.log 2>&1 &
echo $! > logs/start_controllers.pid

echo "DRL-OR-S Routing Suite started"
echo "server socket: 6001"
echo "Web UI: http://localhost:6009"
echo "DRL path_service: 127.0.0.1:8889"
echo "Starting Military Mininet topology in this terminal..."
echo "Exit the Mininet CLI to stop the suite."

sudo python3 testbed/creat_test_topo.py 2>&1 | tee logs/mininet_topology.log
