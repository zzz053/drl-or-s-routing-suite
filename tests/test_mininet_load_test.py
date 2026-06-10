import json
import subprocess

import tools.mininet_load_test as mininet_load_test
from tools.mininet_load_test import (
    FlowSpec,
    HostNamespace,
    build_flow_specs,
    cleanup_iperf_processes,
    prepare_popen_stdin,
    parse_iperf3_result,
    parse_mininet_host_processes,
    parse_primary_ipv4,
    render_markdown_report,
    run_command,
    summarize_results,
    terminate_server_process,
)


def test_parse_mininet_host_processes_selects_exact_namespace_shells():
    output = "\n".join([
        "111 bash -c pgrep -f mininet:h28",
        "222 bash --norc --noediting -is mininet:h28",
        "333 bash --norc --noediting -is mininet:h31",
        "444 bash --norc --noediting -is mininet:s31",
        "444 python something_else.py",
    ])

    hosts = parse_mininet_host_processes(output)

    assert hosts == {
        "h28": "222",
        "h31": "333",
    }


def test_parse_primary_ipv4_ignores_loopback_and_extracts_host_ip():
    output = "\n".join([
        "1: lo    inet 127.0.0.1/8 scope host lo",
        "2: h28-eth0    inet 10.0.0.28/8 brd 10.255.255.255 scope global h28-eth0",
    ])

    assert parse_primary_ipv4(output) == "10.0.0.28"


def test_build_flow_specs_is_seeded_and_never_uses_same_source_destination():
    hosts = [
        HostNamespace(name="h28", pid="222", ip="10.0.0.28"),
        HostNamespace(name="h31", pid="333", ip="10.0.0.31"),
        HostNamespace(name="h34", pid="444", ip="10.0.0.34"),
    ]

    first = build_flow_specs(hosts, flow_count=5, seed=7, base_port=5201)
    second = build_flow_specs(hosts, flow_count=5, seed=7, base_port=5201)

    assert first == second
    assert len(first) == 5
    assert all(item.src.name != item.dst.name for item in first)
    assert [item.port for item in first] == [5201, 5202, 5203, 5204, 5205]


def test_parse_iperf3_result_handles_tcp_json():
    payload = {
        "end": {
            "sum_received": {
                "bits_per_second": 12345678.0,
                "bytes": 1234,
            }
        }
    }

    result = parse_iperf3_result(json.dumps(payload), protocol="tcp")

    assert result["throughput_bps"] == 12345678.0
    assert result["bytes"] == 1234
    assert result["lost_percent"] is None


def test_parse_iperf3_result_ignores_sudo_prompt_noise():
    payload = {
        "end": {
            "sum_received": {
                "bits_per_second": 123.0,
                "bytes": 12,
            }
        }
    }
    raw = "[sudo] hydrate 的密码： " + json.dumps(payload) + "\n"

    result = parse_iperf3_result(raw, protocol="tcp")

    assert result["throughput_bps"] == 123.0
    assert result["bytes"] == 12


def test_parse_iperf3_result_handles_udp_json():
    payload = {
        "end": {
            "sum": {
                "bits_per_second": 5000000.0,
                "bytes": 500,
                "lost_percent": 2.5,
                "jitter_ms": 0.42,
            }
        }
    }

    result = parse_iperf3_result(json.dumps(payload), protocol="udp")

    assert result["throughput_bps"] == 5000000.0
    assert result["bytes"] == 500
    assert result["lost_percent"] == 2.5
    assert result["jitter_ms"] == 0.42


def test_summarize_results_counts_success_and_total_throughput():
    results = [
        {"status": "pass", "throughput_bps": 10.0},
        {"status": "fail", "throughput_bps": 0.0},
        {"status": "pass", "throughput_bps": 20.0},
    ]

    summary = summarize_results(results)

    assert summary["total_flows"] == 3
    assert summary["passed_flows"] == 2
    assert summary["failed_flows"] == 1
    assert summary["total_throughput_bps"] == 30.0
    assert summary["average_throughput_bps"] == 15.0


def test_render_markdown_report_includes_summary_and_each_flow():
    report = {
        "summary": {
            "total_flows": 1,
            "passed_flows": 1,
            "failed_flows": 0,
            "total_throughput_bps": 1000000.0,
            "average_throughput_bps": 1000000.0,
        },
        "flows": [
            {
                "id": 1,
                "src": "h28",
                "dst": "h31",
                "protocol": "tcp",
                "status": "pass",
                "throughput_bps": 1000000.0,
            }
        ],
    }

    text = render_markdown_report(report)

    assert "# Mininet 随机负载测试报告" in text
    assert "| 1 | h28 | h31 | tcp | pass | 1.00 Mbps |" in text


def test_prepare_popen_stdin_detaches_closed_pipe_after_password_write():
    class FakeStdin:
        def __init__(self):
            self.written = ""
            self.flushed = False
            self.closed = False

        def write(self, value):
            self.written += value

        def flush(self):
            self.flushed = True

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self):
            self.stdin = FakeStdin()

    proc = FakeProc()

    prepare_popen_stdin(proc, "h\n")

    assert proc.stdin is None


def test_terminate_server_process_kills_group_and_unique_iperf_port(monkeypatch):
    calls = []

    class FakeProc:
        pid = 1234

        def poll(self):
            return None

        def wait(self, timeout):
            raise subprocess.TimeoutExpired(["iperf3"], timeout)

        def kill(self):
            calls.append(("kill",))

        def terminate(self):
            calls.append(("terminate",))

    def fake_killpg(pid, sig):
        calls.append(("killpg", pid, sig))

    def fake_run_command(command, timeout=10):
        calls.append(("run_command", command, timeout))
        return 0, ""

    monkeypatch.setattr(mininet_load_test.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(mininet_load_test, "run_command", fake_run_command)
    monkeypatch.setattr(mininet_load_test.time, "sleep", lambda _: None)

    flow = FlowSpec(
        id=9,
        src=HostNamespace("h1", "101", "10.0.0.1"),
        dst=HostNamespace("h2", "102", "10.0.0.2"),
        port=5209,
    )

    terminate_server_process(FakeProc(), flow)

    assert ("killpg", 1234, mininet_load_test.signal.SIGTERM) in calls
    assert ("killpg", 1234, getattr(mininet_load_test.signal, "SIGKILL", mininet_load_test.signal.SIGTERM)) in calls
    assert ("run_command", ["sudo", "pkill", "-TERM", "-f", "iperf3 -s -1 -p 5209"], 5) in calls
    assert ("run_command", ["sudo", "pkill", "-TERM", "-f", "iperf3 .* -p 5209"], 5) in calls
    assert ("run_command", ["sudo", "pkill", "-KILL", "-f", "iperf3 -s -1 -p 5209"], 5) in calls
    assert ("run_command", ["sudo", "pkill", "-KILL", "-f", "iperf3 .* -p 5209"], 5) in calls


def test_cleanup_iperf_processes_targets_server_and_client_by_port(monkeypatch):
    calls = []

    def fake_run_command(command, timeout=10):
        calls.append((command, timeout))
        return 0, ""

    monkeypatch.setattr(mininet_load_test, "run_command", fake_run_command)
    monkeypatch.setattr(mininet_load_test.time, "sleep", lambda _: None)

    flow = FlowSpec(
        id=13,
        src=HostNamespace("h1", "101", "10.0.0.1"),
        dst=HostNamespace("h2", "102", "10.0.0.2"),
        port=5213,
    )

    cleanup_iperf_processes(flow)

    assert (["sudo", "pkill", "-TERM", "-f", "iperf3 -s -1 -p 5213"], 5) in calls
    assert (["sudo", "pkill", "-TERM", "-f", "iperf3 .* -p 5213"], 5) in calls
    assert (["sudo", "pkill", "-KILL", "-f", "iperf3 -s -1 -p 5213"], 5) in calls
    assert (["sudo", "pkill", "-KILL", "-f", "iperf3 .* -p 5213"], 5) in calls


def test_run_command_timeout_output_is_json_serializable_text(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output=b"timeout bytes")

    monkeypatch.setattr(mininet_load_test.subprocess, "run", fake_run)

    code, output = run_command(["iperf3"], timeout=1)

    assert code == 124
    assert output == "timeout bytes"
    json.dumps({"output": output})
