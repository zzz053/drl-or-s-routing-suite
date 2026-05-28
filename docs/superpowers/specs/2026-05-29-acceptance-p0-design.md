# Acceptance P0 Design

## Purpose

Build the minimum acceptance-ready operating layer for this project so a Linux VM can be copied into a restricted customer environment, connected to the real SDN switch network, started with one command, checked with one command, and documented with one generated report.

The scope is intentionally limited to the P0 acceptance requirements:

- One-command start, stop, health check, and report generation.
- VM deployment instructions.
- Static hybrid boundary configuration.
- Automatic Markdown acceptance report generation.
- Web homepage indication of hybrid communication readiness.

This design does not attempt to make the project a full SDN product, replace the existing Ryu/Mininet architecture, or implement new DRL routing behavior.

## Operating Assumptions

- The acceptance VM is Linux.
- The operator is allowed to use `sudo` inside the VM.
- The real switch network can be physically connected to a VM-visible NIC.
- Cross-VM-boundary LLDP cannot be assumed to work.
- The virtual/real boundary link must therefore be statically configured.
- The existing project remains centered on:
  - `server_agent.py`
  - `drl-or-s/path_service.py`
  - `start_controllers_test.py`
  - `testbed/creat_test_topo.py`
  - Flask Web UI on port `6009`

## Recommended Approach

Use a shell-based acceptance entrypoint with Python helper tools:

```bash
./acceptance.sh start
./acceptance.sh stop
./acceptance.sh health
./acceptance.sh report
```

The entrypoint is a Bash script because Mininet, OVS, process management, and sudo boundary operations are already shell-oriented in this project. Python helpers handle structured configuration, health evaluation, and Markdown report generation.

The operator should run `./acceptance.sh start` as a normal user. The script uses `sudo` only for Mininet/OVS operations that require root privileges. `server_agent`, `path_service`, and Ryu controllers run as the normal project user to avoid root-owned logs, pid files, and Python environment problems.

## Configuration

Add a standard-library-friendly JSON config:

```text
config/hybrid_acceptance.json
```

Initial structure:

```json
{
  "external_interface": "eno1",
  "controllers": {
    "ports": [6654, 6655, 6656, 6657, 6658, 6659, 6670],
    "forbidden_ports": [6671]
  },
  "hybrid": {
    "external_link_ports": [
      {"dpid": 1, "port": 20}
    ],
    "gateway_ip": "10.0.0.254",
    "gateway_mac": "02:00:00:00:fe:01",
    "real_routes": ["192.168.103.0/24"]
  },
  "validation": {
    "virtual_host_name": "h28",
    "virtual_host_ip": "10.0.0.28",
    "real_host_ip": "192.168.103.3",
    "expected_real_switch_dpid": 128986965761
  }
}
```

JSON is preferred over YAML because the Python standard library can parse it without adding dependencies. This matters in a sealed acceptance VM.

The config maps to existing runtime environment variables:

- `EXTERNAL_LINK_PORTS=1:20`
- `HYBRID_GATEWAY_IP=10.0.0.254`
- `HYBRID_GATEWAY_MAC=02:00:00:00:fe:01`
- `HYBRID_REAL_ROUTES=192.168.103.0/24`

## Entrypoint Behavior

### `./acceptance.sh start`

Responsibilities:

1. Load `config/hybrid_acceptance.json`.
2. Validate required files and tools exist.
3. Validate the configured external interface exists.
4. Export runtime environment variables from the config.
5. Start `path_service` with `nohup setsid`.
6. Start `server_agent.py hybrid` with `nohup setsid`.
7. Start seven Ryu controllers with `start_controllers_test.py start -n`.
8. Start Mininet in background with the configured external interface.
9. Write pid files under `logs/`.
10. Print the exact commands for health and report generation.

The Mininet background process should follow the already validated pattern:

```bash
tail -f /dev/null | sudo -E python3 -u testbed/creat_test_topo.py "$EXTERNAL_INTF"
```

The `-E` is needed so sudo preserves `EXTERNAL_LINK_PORTS`, `HYBRID_GATEWAY_IP`, and `HYBRID_REAL_ROUTES` for topology startup.

### `./acceptance.sh stop`

Responsibilities:

1. Stop Ryu controllers through `start_controllers_test.py stop`.
2. Kill pid-file-managed background processes.
3. Run `sudo mn -c` to clean Mininet/OVS residue.
4. Leave source files, config, reports, and committed code untouched.

Stop must be idempotent. Running it multiple times should not fail the acceptance environment.

### `./acceptance.sh health`

Responsibilities:

1. Run the Python health checker.
2. Print a human-readable Chinese summary.
3. Exit non-zero only when control-plane requirements fail.

Health checks are split into two levels:

Control-plane checks:

- `server_agent` port `6001` is listening.
- Web port `6009` is listening.
- `path_service` port `8889` is listening.
- Ryu ports `6654,6655,6656,6657,6658,6659,6670` are listening.
- Forbidden port `6671` is not listening.
- `/api/health` responds.
- `/api/statistics` responds.
- `/api/acceptance/status` responds.
- Recent logs contain no new severe errors such as `Traceback`, `AttributeError`, `root_disconnected`, `local variable`, or barrier handler exceptions.

Data-plane checks:

- The configured Mininet host namespace exists, for example `h28`.
- The configured route exists, for example `192.168.103.0/24 via 10.0.0.254`.
- Warmup ping from `h28` to `192.168.103.3` runs.
- Verification ping from `h28` to `192.168.103.3` runs.
- OVS flow checks confirm `s28` and `s1` contain `idle_timeout=120` flows for `10.0.0.28 <-> 192.168.103.3`.

If data-plane checks cannot run because Mininet is not active or sudo is unavailable, the result is `risk`, not an immediate `fail`, as long as the control plane is healthy.

### `./acceptance.sh report`

Responsibilities:

1. Run the same health checks as `health`.
2. Generate a Markdown report under:

```text
reports/acceptance-report-YYYYMMDD-HHMMSS.md
```

3. Print the report path.

The report must include:

- Config summary.
- Service port status.
- Controller status.
- Web API status.
- Hybrid boundary status.
- h28 route output.
- Warmup ping result.
- Verification ping result.
- s28/s1 flow snippets.
- Recent severe log lines.
- Final conclusion: `通过`, `有风险`, or `失败`.

## Python Helper Modules

### `tools/acceptance_config.py`

Responsibilities:

- Load JSON config.
- Validate required sections.
- Convert `external_link_ports` to `EXTERNAL_LINK_PORTS` string.
- Convert `real_routes` to `HYBRID_REAL_ROUTES` string.
- Provide defaults only when safe:
  - `controllers.ports`
  - `controllers.forbidden_ports`
  - `hybrid.gateway_ip`
  - `hybrid.gateway_mac`

It must not silently guess `external_interface` or `real_host_ip`.

### `tools/acceptance_health.py`

Responsibilities:

- Read the config.
- Run local checks.
- Optionally run Mininet/OVS checks when available.
- Return structured JSON for scripts.
- Print a concise Chinese summary by default.

Proposed CLI:

```bash
python3 tools/acceptance_health.py --config config/hybrid_acceptance.json
python3 tools/acceptance_health.py --config config/hybrid_acceptance.json --json
```

Exit codes:

- `0`: pass
- `1`: fail
- `2`: risk

### `tools/generate_acceptance_report.py`

Responsibilities:

- Call the health checker programmatically.
- Render a Markdown report.
- Save it under `reports/`.
- Return the report path.

Proposed CLI:

```bash
python3 tools/generate_acceptance_report.py --config config/hybrid_acceptance.json
```

## Web Acceptance Status

Add:

```text
GET /api/acceptance/status
```

The endpoint must not execute ping or sudo operations. It reports control-plane readiness and recent route-session evidence.

Example response:

```json
{
  "status": "ready",
  "virtual_host_ip": "10.0.0.28",
  "real_host_ip": "192.168.103.3",
  "controllers_expected": 7,
  "controllers_connected": 7,
  "drl_connected": true,
  "hybrid_gateway_ip": "10.0.0.254",
  "real_routes": ["192.168.103.0/24"],
  "recent_route_session": {
    "src_ip": "10.0.0.28",
    "dst_ip": "192.168.103.3",
    "path_source": "dijkstra"
  },
  "issues": []
}
```

Status values:

- `ready`: control plane is ready and a matching recent route session exists.
- `partial`: control plane is ready but no matching route session exists.
- `not_ready`: controllers, DRL service, or graph state is incomplete.
- `unknown`: config cannot be loaded.

The Web homepage should add a small acceptance status card near the top:

```text
虚实通信状态：ready / partial / not_ready / unknown
虚拟主机：10.0.0.28
真实主机：192.168.103.3
控制器：7/7
DRL服务：已连接/未连接
最近路径：10.0.0.28 -> 192.168.103.3
```

The card must clearly label itself as “控制面/最近路径状态”, not as a live ping result.

## VM Deployment Documentation

Add:

```text
docs/vm-acceptance-deployment.md
```

Required sections:

1. VM copy and first boot.
2. Required Linux packages and Python environment.
3. Network adapter mode and interface naming.
4. Real switch cabling.
5. Editing `config/hybrid_acceptance.json`.
6. Starting the system.
7. Running health check.
8. Generating report.
9. Troubleshooting:
   - wrong NIC name
   - missing sudo permission
   - `6671` unexpectedly listening
   - missing Ryu port
   - no route session
   - first ping fails but second ping passes
   - LLDP across VM boundary not working

## Error Handling

The acceptance tools should produce actionable failures:

- Missing config file: print exact path and expected command.
- Invalid JSON: print line/column from `json.JSONDecodeError`.
- Missing external interface: print current `ip link` interface names.
- Port missing: list expected and actual listening ports.
- Forbidden port listening: mark fail and show process when available.
- Web API unavailable: show URL and curl failure.
- Data-plane check unavailable: mark risk and explain whether Mininet or sudo was missing.
- Ping failed: include warmup and verification outputs.
- Flow check failed: show actual `ovs-ofctl dump-flows` snippets when available.

## Testing Strategy

Use test-first implementation.

Tests should cover:

- Config parsing and validation.
- `EXTERNAL_LINK_PORTS` formatting.
- `HYBRID_REAL_ROUTES` formatting.
- Health status classification:
  - all checks pass
  - control-plane fail
  - data-plane unavailable
  - forbidden port listening
- Report rendering includes required sections.
- `acceptance.sh` contains required command cases and uses config-driven environment variables.
- Web API exposes `/api/acceptance/status`.
- Web UI includes the acceptance status card and does not claim live ping status.

Runtime integration with real Mininet/OVS should be verified manually because it requires root and the lab network. The automated tests should mock command outputs or test pure parsing/classification logic.

## Scope Boundaries

Included in P0:

- Local VM acceptance flow.
- Static hybrid boundary config.
- Local health/report tooling.
- Web control-plane status card.
- Documentation.

Excluded from P0:

- SSH into real switches.
- Automatic real switch configuration.
- Full YAML support.
- Full Python CLI replacing shell.
- DRL model behavior changes.
- Browser-driven topology redesign.
- Multi-tenant or production deployment hardening.

## Success Criteria

The P0 work is complete when:

1. A fresh operator can edit `config/hybrid_acceptance.json`.
2. `./acceptance.sh start` starts the acceptance environment.
3. `./acceptance.sh health` reports pass/risk/fail with actionable messages.
4. `./acceptance.sh report` generates a Markdown report under `reports/`.
5. Web UI shows a hybrid acceptance status card.
6. `./acceptance.sh stop` cleans up the running environment.
7. Core tests and syntax checks pass.
8. The tools do not depend on non-standard Python packages beyond the project’s existing runtime.
