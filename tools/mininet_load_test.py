#!/usr/bin/env python3
"""Random Mininet host traffic generator for load testing.

The script expects an already running Mininet topology. It discovers Mininet
host namespaces from their shell processes and runs concurrent iperf3 flows
between randomly selected host pairs.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import time
from typing import Iterable


@dataclass(frozen=True)
class HostNamespace:
    name: str
    pid: str
    ip: str


@dataclass(frozen=True)
class FlowSpec:
    id: int
    src: HostNamespace
    dst: HostNamespace
    port: int


def parse_mininet_host_processes(ps_output: str) -> dict[str, str]:
    hosts: dict[str, str] = {}
    for line in (ps_output or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            pid, args = stripped.split(None, 1)
        except ValueError:
            continue
        prefix = "bash --norc --noediting -is mininet:"
        if pid.isdigit() and args.startswith(prefix):
            host_name = args[len(prefix):].strip()
            if host_name.startswith("h"):
                hosts[host_name] = pid
    return hosts


def parse_primary_ipv4(ip_addr_output: str) -> str | None:
    for line in (ip_addr_output or "").splitlines():
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/\d+", line)
        if not match:
            continue
        address = match.group(1)
        if not address.startswith("127."):
            return address
    return None


def _sudo_command(command: list[str]) -> tuple[list[str], str | None]:
    if command and command[0] == "sudo" and os.environ.get("SUDO_PASSWORD"):
        return ["sudo", "-S", "-p", ""] + command[1:], os.environ["SUDO_PASSWORD"] + "\n"
    return command, None


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def run_command(command: list[str], timeout: int = 10) -> tuple[int, str]:
    command, input_text = _sudo_command(command)
    try:
        completed = subprocess.run(
            command,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or ""
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired as exc:
        output = _to_text(exc.stdout) or _to_text(exc.stderr)
        return 124, output or f"timeout after {timeout}s"


def prepare_popen_stdin(proc, input_text: str | None) -> None:
    if input_text and proc.stdin:
        proc.stdin.write(input_text)
        proc.stdin.flush()
        proc.stdin.close()
        # subprocess.communicate() flushes stdin again when the attribute is
        # still set. Detach it after writing the sudo password.
        proc.stdin = None


def discover_mininet_hosts(include: set[str] | None = None, exclude: set[str] | None = None) -> list[HostNamespace]:
    code, output = run_command(["ps", "-eo", "pid=,args="], timeout=5)
    if code != 0:
        raise RuntimeError(f"failed to list processes: {output}")

    hosts = []
    include = include or set()
    exclude = exclude or set()
    for name, pid in sorted(parse_mininet_host_processes(output).items()):
        if include and name not in include:
            continue
        if name in exclude:
            continue
        code, ip_output = run_command(["sudo", "mnexec", "-a", pid, "ip", "-4", "-o", "addr", "show"], timeout=5)
        if code != 0:
            continue
        ip = parse_primary_ipv4(ip_output)
        if ip:
            hosts.append(HostNamespace(name=name, pid=pid, ip=ip))
    return hosts


def ensure_iperf3_available() -> None:
    code, output = run_command(["iperf3", "--version"], timeout=5)
    if code != 0:
        raise RuntimeError(f"iperf3 is required for load testing: {output}")


def _parse_csv_set(raw: str | None) -> set[str]:
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def build_flow_specs(
    hosts: list[HostNamespace],
    flow_count: int,
    seed: int | None,
    base_port: int = 5201,
) -> list[FlowSpec]:
    if len(hosts) < 2:
        raise ValueError("at least two Mininet hosts are required")
    rng = random.Random(seed)
    flows = []
    for idx in range(int(flow_count)):
        src, dst = rng.sample(hosts, 2)
        flows.append(FlowSpec(id=idx + 1, src=src, dst=dst, port=base_port + idx))
    return flows


def parse_iperf3_result(raw_json: str, protocol: str) -> dict:
    start = raw_json.find("{")
    end_pos = raw_json.rfind("}")
    if start < 0 or end_pos < start:
        raise ValueError("iperf3 output did not contain a JSON object")
    payload = json.loads(raw_json[start:end_pos + 1])
    end = payload.get("end") or {}
    if protocol == "udp":
        summary = end.get("sum") or {}
    else:
        summary = end.get("sum_received") or end.get("sum_sent") or {}
    return {
        "throughput_bps": float(summary.get("bits_per_second") or 0.0),
        "bytes": int(summary.get("bytes") or 0),
        "lost_percent": summary.get("lost_percent"),
        "jitter_ms": summary.get("jitter_ms"),
    }


def _start_server(flow: FlowSpec) -> subprocess.Popen:
    command = [
        "sudo", "mnexec", "-a", flow.dst.pid,
        "iperf3", "-s", "-1", "-p", str(flow.port), "--json",
    ]
    command, input_text = _sudo_command(command)
    proc = subprocess.Popen(
        command,
        text=True,
        stdin=subprocess.PIPE if input_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    prepare_popen_stdin(proc, input_text)
    return proc


def _run_client(flow: FlowSpec, protocol: str, duration: int, bandwidth: str | None, timeout: int) -> tuple[int, str]:
    command = [
        "sudo", "mnexec", "-a", flow.src.pid,
        "iperf3", "-c", flow.dst.ip, "-p", str(flow.port),
        "-t", str(duration), "--json",
    ]
    if protocol == "udp":
        command.extend(["-u", "-b", bandwidth or "10M"])
    return run_command(command, timeout=timeout)


def cleanup_iperf_processes(flow: FlowSpec) -> None:
    for pattern in [
        f"iperf3 -s -1 -p {flow.port}",
        f"iperf3 .* -p {flow.port}",
    ]:
        run_command(["sudo", "pkill", "-TERM", "-f", pattern], timeout=5)
    time.sleep(0.1)
    for pattern in [
        f"iperf3 -s -1 -p {flow.port}",
        f"iperf3 .* -p {flow.port}",
    ]:
        run_command(["sudo", "pkill", "-KILL", "-f", pattern], timeout=5)


def terminate_server_process(proc: subprocess.Popen, flow: FlowSpec) -> None:
    if proc.poll() is None:
        try:
            killpg = getattr(os, "killpg", None)
            if killpg:
                killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            pass
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                killpg = getattr(os, "killpg", None)
                if killpg:
                    killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                else:
                    proc.kill()
            except ProcessLookupError:
                pass
            except OSError:
                proc.kill()

    # iperf3 may survive sudo/mnexec termination as an orphaned process in the
    # host namespace. Clean by this test's unique port so later flows cannot hang.
    cleanup_iperf_processes(flow)


def run_flow(flow: FlowSpec, protocol: str, duration: int, bandwidth: str | None, server_ready_delay: float) -> dict:
    started_at = datetime.now().isoformat(timespec="seconds")
    server = _start_server(flow)
    time.sleep(max(float(server_ready_delay), 0.0))

    client_timeout = max(int(duration) + 15, 20)
    code, client_output = _run_client(flow, protocol, duration, bandwidth, timeout=client_timeout)
    if code != 0:
        cleanup_iperf_processes(flow)
    try:
        server_stdout, server_stderr = server.communicate(timeout=client_timeout)
    except subprocess.TimeoutExpired:
        terminate_server_process(server, flow)
        try:
            server_stdout, server_stderr = server.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            server_stdout, server_stderr = "", "server process did not exit cleanly after forced cleanup"

    base = {
        "id": flow.id,
        "src": flow.src.name,
        "src_ip": flow.src.ip,
        "dst": flow.dst.name,
        "dst_ip": flow.dst.ip,
        "port": flow.port,
        "protocol": protocol,
        "started_at": started_at,
        "status": "fail",
        "throughput_bps": 0.0,
        "bytes": 0,
        "lost_percent": None,
        "jitter_ms": None,
        "client_returncode": code,
        "client_output": client_output,
        "server_output": server_stdout,
        "server_error": server_stderr,
    }
    if code != 0:
        return base
    try:
        parsed = parse_iperf3_result(client_output, protocol)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        base["client_output"] = f"{client_output}\nparse_error={exc}"
        return base
    base.update(parsed)
    base["status"] = "pass" if base["throughput_bps"] > 0 else "fail"
    return base


def run_load_test(
    hosts: list[HostNamespace],
    flow_count: int,
    duration: int,
    parallel: int,
    protocol: str,
    bandwidth: str | None,
    seed: int | None,
    base_port: int,
    server_ready_delay: float,
) -> dict:
    flows = build_flow_specs(hosts, flow_count=flow_count, seed=seed, base_port=base_port)
    results = []
    with ThreadPoolExecutor(max_workers=max(int(parallel), 1)) as executor:
        futures = [
            executor.submit(run_flow, flow, protocol, duration, bandwidth, server_ready_delay)
            for flow in flows
        ]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["id"])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": {
            "flows": flow_count,
            "duration": duration,
            "parallel": parallel,
            "protocol": protocol,
            "bandwidth": bandwidth,
            "seed": seed,
            "base_port": base_port,
        },
        "hosts": [asdict(host) for host in hosts],
        "flows": results,
        "summary": summarize_results(results),
    }


def summarize_results(results: Iterable[dict]) -> dict:
    items = list(results)
    passed = [item for item in items if item.get("status") == "pass"]
    failed = len(items) - len(passed)
    total_bps = sum(float(item.get("throughput_bps") or 0.0) for item in passed)
    return {
        "total_flows": len(items),
        "passed_flows": len(passed),
        "failed_flows": failed,
        "total_throughput_bps": total_bps,
        "average_throughput_bps": total_bps / len(passed) if passed else 0.0,
        "min_throughput_bps": min((float(item.get("throughput_bps") or 0.0) for item in passed), default=0.0),
        "max_throughput_bps": max((float(item.get("throughput_bps") or 0.0) for item in passed), default=0.0),
    }


def _format_bps(value: float | int | None) -> str:
    bps = float(value or 0.0)
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Gbps"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.2f} Kbps"
    return f"{bps:.2f} bps"


def render_markdown_report(report: dict) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Mininet 随机负载测试报告",
        "",
        f"- 生成时间：{report.get('generated_at', '-')}",
        f"- 总流数：{summary.get('total_flows', 0)}",
        f"- 成功流数：{summary.get('passed_flows', 0)}",
        f"- 失败流数：{summary.get('failed_flows', 0)}",
        f"- 总吞吐：{_format_bps(summary.get('total_throughput_bps'))}",
        f"- 平均吞吐：{_format_bps(summary.get('average_throughput_bps'))}",
        "",
        "## 流结果",
        "",
        "| ID | 源主机 | 目的主机 | 协议 | 状态 | 吞吐 |",
        "| ---: | --- | --- | --- | --- | ---: |",
    ]
    for item in report.get("flows", []):
        lines.append(
            "| {id} | {src} | {dst} | {protocol} | {status} | {throughput} |".format(
                id=item.get("id", ""),
                src=item.get("src", ""),
                dst=item.get("dst", ""),
                protocol=item.get("protocol", ""),
                status=item.get("status", ""),
                throughput=_format_bps(item.get("throughput_bps")),
            )
        )
    return "\n".join(lines) + "\n"


def write_reports(report: dict, report_dir: str | Path) -> tuple[Path, Path]:
    path = Path(report_dir)
    path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = path / f"load-test-{timestamp}.json"
    md_path = path / f"load-test-{timestamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, md_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run random iperf3 load tests between Mininet hosts.")
    default_seed = os.environ.get("LOAD_TEST_SEED") or None
    parser.add_argument("--flows", type=int, default=int(os.environ.get("LOAD_TEST_FLOWS", "20")))
    parser.add_argument("--duration", type=int, default=int(os.environ.get("LOAD_TEST_DURATION", "10")))
    parser.add_argument("--parallel", type=int, default=int(os.environ.get("LOAD_TEST_PARALLEL", "5")))
    parser.add_argument("--seed", type=int, default=int(default_seed) if default_seed is not None else None)
    parser.add_argument("--base-port", type=int, default=5201)
    parser.add_argument("--udp", action="store_true", default=os.environ.get("LOAD_TEST_UDP", "0") == "1")
    parser.add_argument("--bandwidth", default=os.environ.get("LOAD_TEST_BANDWIDTH", "10M"))
    parser.add_argument("--include-hosts", default="")
    parser.add_argument("--exclude-hosts", default="")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--server-ready-delay", type=float, default=0.3)
    args = parser.parse_args(argv)

    include = _parse_csv_set(args.include_hosts)
    exclude = _parse_csv_set(args.exclude_hosts)
    try:
        ensure_iperf3_available()
    except RuntimeError as exc:
        print(f"mininet load test failed: {exc}", file=sys.stderr)
        return 2
    hosts = discover_mininet_hosts(include=include, exclude=exclude)
    if len(hosts) < 2:
        print("mininet load test failed: fewer than two usable Mininet hosts discovered", file=sys.stderr)
        return 2

    protocol = "udp" if args.udp else "tcp"
    report = run_load_test(
        hosts=hosts,
        flow_count=args.flows,
        duration=args.duration,
        parallel=args.parallel,
        protocol=protocol,
        bandwidth=args.bandwidth if args.udp else None,
        seed=args.seed,
        base_port=args.base_port,
        server_ready_delay=args.server_ready_delay,
    )
    json_path, md_path = write_reports(report, args.report_dir)
    print(render_markdown_report(report))
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0 if report["summary"]["failed_flows"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
