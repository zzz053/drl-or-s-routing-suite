# DRL-OR-S Routing Suite

This is the deliverable package for the merged DRL-OR-S multi-domain SDN routing system.

This directory is self-contained: it includes the Web/controller code, Military Mininet topology, DRL path service runtime code, Military topology data, and Military model weights.

## Ports

- server socket: `6001`
- Web UI: `6009`
- DRL path_service: `8889`

## Start

Install Python dependencies from this directory:

```bash
pip3 install -r requirements.txt
```

Mininet and Open vSwitch must be installed as system packages in the Linux/Mininet environment.

```bash
./start_suite.sh
```

The script starts DRL, server_agent, the Military Ryu controllers, then enters the Military Mininet CLI in the current terminal. Running it from PyCharm Terminal keeps the Mininet CLI inside PyCharm instead of opening separate Ubuntu terminal windows.

Then open:

```text
http://localhost:6009
```

## Stop

```bash
./stop_suite.sh
```

## Acceptance Topology

Use the Military topology:

```bash
sudo python3 testbed/creat_test_topo.py
```

## 中文运行测试文档

完整运行、测试和排障步骤见：

```text
RUN_TESTING_CN.md
```

## Notes

- The Web UI keeps the `hydrate` design.
- Manual flow add/delete and route sessions are preserved.
- DRL path calculation uses `drl-or-s/path_service.py` with a long-lived socket from `server_agent.py`.
- The Military model weights are packaged under `drl-or-s/model/Military_mininet/`.
- The topology files used by DRL inference are packaged under `drl-or-s/topology/Military/`.
- If DRL is unavailable, server-side path calculation falls back to Dijkstra.
- `new` flow history is intentionally not included because route sessions and flow tables cover the deliverable workflow.
