from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "controller.py"
WEB_UI = ROOT / "web_ui_html.py"


def test_controller_requests_flow_removed_notifications():
    text = CONTROLLER.read_text(encoding="utf-8")

    assert "OFPFF_SEND_FLOW_REM" in text
    assert "flags=flow_mod_flags" in text


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
