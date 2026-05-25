from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_state_store_module_exists_and_versions_snapshots():
    text = (ROOT / "web_state_store.py").read_text(encoding="utf-8")

    assert "class WebStateStore" in text
    assert "topology_version" in text
    assert "route_sessions_version" in text
    assert "flow_versions" in text
    assert "get_graph_snapshot" in text


def test_web_api_serves_cached_graph_versions():
    text = (ROOT / "web_api.py").read_text(encoding="utf-8")

    assert "get_graph_snapshot" in text
    assert "'versions': snapshot.get('versions'" in text
    assert "server_agent.G.nodes(data=True)" not in text


def test_frontend_uses_incremental_graph_updates_and_layout_guard():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "function syncDataSet" in text
    assert "lastTopologySignature" in text
    assert "lastLayoutSignature" in text
    assert "nodes.clear()" not in text
    assert "edges.clear()" not in text
    assert "if (topologySignature !== lastLayoutSignature)" in text


def test_route_session_highlight_uses_delta_updates():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "lastHighlightedNodeIds" in text
    assert "lastHighlightedEdgeIds" in text
    body = text[text.index("function applyRouteSessionHighlight"):text.index("function selectRouteSessionById")]
    assert "nodes.get().forEach" not in body
    assert "edges.get().forEach" not in body


def test_server_agent_topology_update_does_not_dump_full_graph():
    text = (ROOT / "server_agent.py").read_text(encoding="utf-8")
    body = text[text.index("def update_graph"):text.index("def _lookup_host_mac")]

    assert "self.web_state.mark_topology_dirty" in body
    assert "print(f\"**********G" not in body
    assert "logger.info(f\"添加边" not in body
    assert "logger.info(f\"添加主机连接" not in body
def test_web_ui_avoids_optional_chaining_for_server_side_syntax_checks():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "?." not in text


def test_server_agent_topology_message_details_are_debug_only():
    text = (ROOT / "server_agent.py").read_text(encoding="utf-8")

    assert 'logger.info(f"链路详情:' not in text
    assert 'logger.info(f"主机详情:' not in text
