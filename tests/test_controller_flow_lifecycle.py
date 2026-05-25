from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "controller.py"
WEB_UI = ROOT / "web_ui_html.py"


def test_controller_requests_flow_removed_notifications():
    text = CONTROLLER.read_text(encoding="utf-8")

    assert "OFPFF_SEND_FLOW_REM" in text
    assert "flags=flow_mod_flags" in text


def test_controller_auto_route_flows_have_idle_timeout():
    text = CONTROLLER.read_text(encoding="utf-8")

    assert "ROUTE_FLOW_IDLE_TIMEOUT" in text
    assert "idle_timeout=None" in text
    assert "idle_timeout = ROUTE_FLOW_IDLE_TIMEOUT" in text
    assert "'idle_timeout': int(idle_timeout)" in text


def test_cross_domain_path_forwarding_waits_for_openflow_barriers():
    text = CONTROLLER.read_text(encoding="utf-8")

    assert "EventOFPBarrierReply" in text
    assert "def _wait_for_flow_barriers" in text
    assert "barriers_ok = self._wait_for_flow_barriers" in text

    process_start = text.index("def _process_path")
    process_body = text[process_start:text.index("def _request_path", process_start)]
    barrier_pos = process_body.index("barriers_ok = self._wait_for_flow_barriers")
    ack_pos = process_body.index('"type": "path_install_ack"')
    forward_pos = process_body.index("self.send_packet_to_outport(")
    assert barrier_pos < ack_pos < forward_pos


def test_server_waits_when_path_install_ack_reports_barrier_failure():
    text = (ROOT / "server_agent.py").read_text(encoding="utf-8")
    start = text.index("def handle_path_install_ack")
    body = text[start:text.index("def _cleanup_expired_portdata_queries", start)]

    assert "barriers_ok" in body
    assert "path install ACK reported barrier failure" in body
    assert "return" in body[:body.index("pending['acks'].discard")]


def test_controller_handles_flow_removed_events_and_updates_sessions():
    text = CONTROLLER.read_text(encoding="utf-8")

    assert "EventOFPFlowRemoved" in text
    assert "def _flow_removed_handler" in text
    assert "flow_removed" in text
    assert "_remove_flow_from_sessions" in text


def test_link_delete_notifies_root_before_reroute():
    text = CONTROLLER.read_text(encoding="utf-8")
    start = text.index("def delete_link")
    body = text[start:text.index("def switch_features_handler", start)]

    delete_pos = body.index("self.delete_inter_link(link)")
    notify_pos = body.index("self._notify_link_state")
    invalidate_pos = body.index("self._invalidate_sessions_on_link_failure")

    assert delete_pos < notify_pos < invalidate_pos


def test_web_refreshes_selected_switch_flows_after_manual_changes():
    text = WEB_UI.read_text(encoding="utf-8")

    assert "refreshSelectedSwitchFlows" in text
    assert "setInterval(refreshSelectedSwitchFlows" in text
    assert "await loadSwitchFlowsForSidebar(switchId, true)" in text
