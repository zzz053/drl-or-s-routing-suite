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

For hybrid virtual/physical switch communication, pass the host NIC connected to the real SDN switch:

```bash
./start_suite.sh <external-data-plane-nic>
```

This attaches the selected NIC to Mininet `s1:port20`, marks `dpid=1,port=20` as an external link port with `EXTERNAL_LINK_PORTS=1:20`, and keeps the real switch on controller `c1` / OpenFlow port `6654`. In the verified VM environment the NIC is `ens34`; `eno1` is only valid on servers where that physical interface name has been confirmed.

By default, `start_suite.sh` runs `server_agent.py` in `hybrid` mode and uses `$HOME/miniconda3/envs/ryu_drl_s/bin/python` for `path_service.py` when that environment exists. You can override these choices with environment variables:

```bash
PATH_SERVICE_PYTHON=/path/to/python SERVER_AGENT_ROUTE_MODE=shadow ./start_suite.sh eno1
```

Then open:

```text
http://localhost:6009
```

## Stop

```bash
./stop_suite.sh
```

## Source-hidden runtime package

For acceptance delivery where the customer should not browse first-party source
directly, build a runtime layout:

```bash
python tools/build_delivery_package.py --output dist/drl-ors-runtime
```

The generated package keeps editable configuration under
`/etc/drl-ors/config.json`, exposes `drl-orsctl start|stop|health|report`, and
ships core Python modules as `.pyc` files under `/opt/drl-ors`. This is a
lightweight operational hiding measure, not encryption; a root user can still
copy or reverse engineer runtime artifacts.

## Acceptance Topology

Use the Military topology:

```bash
sudo python3 testbed/creat_test_topo.py
```

Hybrid physical attachment:

```bash
sudo EXTERNAL_LINK_PORTS=1:20 python3 testbed/creat_test_topo.py eno1
```

## 中文运行测试文档

完整运行、测试和排障步骤见：

```text
RUN_TESTING_CN.md
```

## Notes

- The Web UI keeps the `hydrate` design.
- Web is read-only by default in the acceptance configuration; manual flow add/delete code is preserved for explicit development mode.
- Link capacity is sensed from OpenFlow port description stats when available, with JSON `bandwidth_mbps` used only as a fallback.
- DRL path calculation uses `drl-or-s/path_service.py` with a long-lived socket from `server_agent.py`.
- The Military model weights are packaged under `drl-or-s/model/Military_mininet/`.
- The topology files used by DRL inference are packaged under `drl-or-s/topology/Military/`.
- If DRL is unavailable, server-side path calculation falls back to Dijkstra.
- `new` flow history is intentionally not included because route sessions and flow tables cover the deliverable workflow.
