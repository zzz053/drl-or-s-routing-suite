# DRL Hybrid Routing Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep DRL in the path-planning loop while making route installation stable, observable, and safely backed by Dijkstra/KSP fallback.

**Architecture:** Preserve the current `controller.py -> server_agent.py -> drl-or-s/path_service.py -> controller.py` flow. Add explicit route decision metadata, introduce K-shortest-path candidates, let DRL act as a path advisor/selector, validate the selected route, and fall back to Dijkstra when DRL is unavailable, invalid, or out of scope.

**Tech Stack:** Python 3, Ryu, Mininet, NetworkX, PyTorch, existing DRL-OR-S `Policy`/`NetEnv`, socket JSON protocol, Flask Web/API.

---

## Current Findings

- `server_agent.py` already tries DRL first in `handle_path_request()` and falls back to `handle_path_request_with_policy()`.
- `server_path_service.py` fallback uses `networkx.shortest_path()` with `compute_edge_weight()`.
- `drl-or-s/path_service.py` only performs true model inference when `actor_critic` is loaded and `compute_path_with_drl()` reaches `actor_critic.act()`.
- `drl-or-s/path_service.py` can silently fall back to `env.calcSHR()` or Dijkstra, but the outer `server_agent.py` currently still treats a successful path_service response as `path_source: drl`.
- `common_config.py` currently maps all task types to `shortest_path`, so business-aware routing is not really enabled yet.

## Target Modes

- `spf`: use current Dijkstra/shortest-path fallback only.
- `shadow`: compute DRL advice, record it, but install Dijkstra/KSP path.
- `hybrid`: prefer validated DRL advice, fall back to Dijkstra/KSP.
- `drl`: force DRL for experiments only; never use as the default acceptance mode.

## File Structure

- Modify `common_config.py`
  - Add DRL routing mode environment flags and thresholds.
  - Add task-to-policy mappings for future low-delay, high-bandwidth, and balanced policies.

- Modify `server_path_service.py`
  - Add K-shortest-path candidate generation.
  - Add path metrics summarization for delay, loss, bandwidth, utilization, and hop count.
  - Add strict path validation helper shared by DRL and fallback responses.

- Modify `server_agent.py`
  - Send route mode, candidate paths, and metrics to `path_service`.
  - Preserve and forward `decision_source`, `model_used`, `fallback_reason`, and `compute_time`.
  - In `shadow` mode, record DRL advice while installing fallback path.

- Modify `drl-or-s/path_service.py`
  - Return truthful decision metadata.
  - Distinguish true model inference from `calcSHR()` and Dijkstra fallback.
  - Accept candidate paths in the request, initially using the existing DRL path as advice and later supporting selector training.

- Modify `controller.py`
  - Store route decision metadata in `route_sessions`.
  - Include route source in topology reports sent back to `server_agent`.

- Modify `web_api.py` and `web_ui_html.py`
  - Expose and display route source, fallback reason, and whether the model was actually used.

- Add tests under `tests/`
  - Cover KSP generation, metadata propagation, fallback behavior, and path validation.

---

### Task 1: Add Routing Mode Configuration

**Files:**
- Modify: `common_config.py`
- Test: `tests/test_drl_routing_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_drl_routing_config.py`:

```python
import importlib


def test_default_drl_route_mode_is_shadow_or_hybrid_safe(monkeypatch):
    monkeypatch.delenv("DRL_ROUTE_MODE", raising=False)
    import common_config
    importlib.reload(common_config)

    assert common_config.DRL_ROUTE_MODE in {"shadow", "hybrid"}


def test_drl_route_mode_accepts_environment_override(monkeypatch):
    monkeypatch.setenv("DRL_ROUTE_MODE", "spf")
    import common_config
    importlib.reload(common_config)

    assert common_config.DRL_ROUTE_MODE == "spf"


def test_drl_thresholds_are_available(monkeypatch):
    monkeypatch.setenv("DRL_INFERENCE_TIMEOUT_MS", "80")
    monkeypatch.setenv("DRL_MIN_CONFIDENCE", "0.65")
    import common_config
    importlib.reload(common_config)

    assert common_config.DRL_INFERENCE_TIMEOUT_MS == 80
    assert common_config.DRL_MIN_CONFIDENCE == 0.65
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_drl_routing_config.py -v
```

Expected: FAIL because `DRL_ROUTE_MODE`, `DRL_INFERENCE_TIMEOUT_MS`, and `DRL_MIN_CONFIDENCE` do not exist yet.

- [ ] **Step 3: Add configuration constants**

In `common_config.py`, add these constants after `PATH_SERVICE_PORT`:

```python
DRL_ROUTE_MODE = os.environ.get("DRL_ROUTE_MODE", "shadow").strip().lower()
if DRL_ROUTE_MODE not in {"spf", "shadow", "hybrid", "drl"}:
    DRL_ROUTE_MODE = "shadow"

DRL_K_CANDIDATES = int(os.environ.get("DRL_K_CANDIDATES", "5"))
DRL_INFERENCE_TIMEOUT_MS = int(os.environ.get("DRL_INFERENCE_TIMEOUT_MS", "100"))
DRL_MIN_CONFIDENCE = float(os.environ.get("DRL_MIN_CONFIDENCE", "0.50"))
```

Keep default as `shadow` for first rollout. Switch to `hybrid` only after smoke tests confirm truthful metadata and stable fallback.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_drl_routing_config.py -v
```

Expected: PASS.

---

### Task 2: Add K-Shortest Candidate Generation

**Files:**
- Modify: `server_path_service.py`
- Test: `tests/test_server_path_service_candidates.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_path_service_candidates.py`:

```python
import networkx as nx

from server_path_service import build_k_shortest_candidates


def test_build_k_shortest_candidates_returns_weighted_simple_paths():
    graph = nx.DiGraph()
    graph.add_edge("h1", 1, weight=1, edge_type="host_link")
    graph.add_edge(1, 2, weight=1, edge_type="switch_link", delay=1, bw=10, loss=0)
    graph.add_edge(2, "h2", weight=1, edge_type="host_link")
    graph.add_edge(1, 3, weight=2, edge_type="switch_link", delay=2, bw=8, loss=0)
    graph.add_edge(3, 2, weight=2, edge_type="switch_link", delay=2, bw=8, loss=0)

    candidates = build_k_shortest_candidates(graph, "h1", "h2", k=2)

    assert [item["path"] for item in candidates] == [
        ["h1", 1, 2, "h2"],
        ["h1", 1, 3, 2, "h2"],
    ]
    assert candidates[0]["path_id"] == 0
    assert candidates[0]["metrics"]["hop_count"] == 3


def test_build_k_shortest_candidates_excludes_down_links():
    graph = nx.DiGraph()
    graph.add_edge("h1", 1, weight=1, edge_type="host_link")
    graph.add_edge(1, 2, weight=1, edge_type="switch_link")
    graph.add_edge(2, "h2", weight=1, edge_type="host_link")
    graph.add_edge(1, 3, weight=1, edge_type="switch_link")
    graph.add_edge(3, 2, weight=1, edge_type="switch_link")

    candidates = build_k_shortest_candidates(graph, "h1", "h2", k=3, link_down_set={(1, 2)})

    assert candidates[0]["path"] == ["h1", 1, 3, 2, "h2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_server_path_service_candidates.py -v
```

Expected: FAIL because `build_k_shortest_candidates()` does not exist yet.

- [ ] **Step 3: Implement candidate generation**

In `server_path_service.py`, add:

```python
def summarize_path_metrics(graph, path):
    switch_edges = []
    for src, dst in zip(path, path[1:]):
        data = graph.get_edge_data(src, dst, default={}) or {}
        if data.get("edge_type") == "switch_link" or (_is_switch_node(src) and _is_switch_node(dst)):
            switch_edges.append(data)

    delay = sum(float(edge.get("delay", edge.get("weight", 1))) for edge in switch_edges)
    loss = 1.0
    bandwidths = []
    utilization = []
    for edge in switch_edges:
        loss *= 1.0 - max(float(edge.get("loss", 0)), 0.0)
        if "bw" in edge:
            bandwidths.append(float(edge.get("bw", 0)))
        if "utilization" in edge:
            utilization.append(float(edge.get("utilization", 0)))

    return {
        "hop_count": max(len(path) - 1, 0),
        "switch_hop_count": len(switch_edges),
        "delay": delay,
        "loss": max(0.0, 1.0 - loss),
        "min_bandwidth": min(bandwidths) if bandwidths else None,
        "max_utilization": max(utilization) if utilization else None,
    }


def build_k_shortest_candidates(graph, src, dst, k=5, link_down_set=None, route_policy="shortest_path"):
    link_down_set = link_down_set or set()
    candidate_graph = graph.copy()
    for u, v in link_down_set:
        if candidate_graph.has_edge(u, v):
            candidate_graph.remove_edge(u, v)

    def _weight(u, v, data):
        return compute_edge_weight(route_policy, data)

    try:
        paths_iter = nx.shortest_simple_paths(candidate_graph, src, dst, weight=_weight)
        candidates = []
        for path_id, path in enumerate(paths_iter):
            candidates.append({
                "path_id": path_id,
                "path": list(path),
                "switch_path": [node for node in path if _is_switch_node(node)],
                "metrics": summarize_path_metrics(graph, path),
            })
            if len(candidates) >= max(int(k), 1):
                break
        return candidates
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_server_path_service_candidates.py -v
```

Expected: PASS.

---

### Task 3: Return Truthful Decision Metadata From Path Service

**Files:**
- Modify: `drl-or-s/path_service.py`
- Test: `tests/test_path_service_decision_metadata.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_path_service_decision_metadata.py`:

```python
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH_SERVICE = ROOT / "drl-or-s" / "path_service.py"


def load_path_service_module():
    spec = importlib.util.spec_from_file_location("path_service_under_test", PATH_SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decision_metadata_helper_marks_model_path():
    module = load_path_service_module()

    result = module._decision("drl_model", [1, 2, 3], model_used=True, fallback_reason=None)

    assert result["path"] == [1, 2, 3]
    assert result["decision_source"] == "drl_model"
    assert result["model_used"] is True
    assert result["fallback_reason"] is None


def test_decision_metadata_helper_marks_fallback():
    module = load_path_service_module()

    result = module._decision("dijkstra", [1, 4], model_used=False, fallback_reason="out_of_drl_range")

    assert result["decision_source"] == "dijkstra"
    assert result["model_used"] is False
    assert result["fallback_reason"] == "out_of_drl_range"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_path_service_decision_metadata.py -v
```

Expected: FAIL because `_decision()` does not exist.

- [ ] **Step 3: Add decision helper**

In `drl-or-s/path_service.py`, add near `_dijkstra_on_edges()`:

```python
def _decision(decision_source, path, model_used=False, fallback_reason=None, confidence=None):
    return {
        "path": path,
        "decision_source": decision_source,
        "model_used": bool(model_used),
        "fallback_reason": fallback_reason,
        "confidence": confidence,
    }
```

- [ ] **Step 4: Change `compute_path()` to return metadata**

Change `compute_path()` so it returns a decision dictionary instead of only a path list:

```python
return _decision("drl_model", path_1based, model_used=True)
```

When `actor_critic is None` but `env.calcSHR()` returns a path:

```python
return _decision("drl_shr", path_1based, model_used=False, fallback_reason="model_not_loaded")
```

When `_dijkstra_on_edges()` returns a path:

```python
return _decision("dijkstra", path, model_used=False, fallback_reason="out_of_drl_range_or_drl_failed")
```

When no path exists:

```python
return _decision("none", None, model_used=False, fallback_reason="no_path")
```

- [ ] **Step 5: Update socket response assembly**

In `handle_client()`, replace:

```python
path = self.compute_path(src_node, dst_node, topo_edges)
```

with:

```python
decision = self.compute_path(src_node, dst_node, topo_edges)
path = decision.get("path") if isinstance(decision, dict) else decision
```

Then include metadata in the response:

```python
response = {
    "type": "path_response",
    "status": "ok" if path else "error",
    "path": path,
    "request_id": request_id,
    "compute_time": elapsed,
    "decision_source": decision.get("decision_source", "unknown"),
    "model_used": decision.get("model_used", False),
    "fallback_reason": decision.get("fallback_reason"),
    "confidence": decision.get("confidence"),
}
```

- [ ] **Step 6: Run tests**

Run:

```bash
pytest tests/test_path_service_decision_metadata.py -v
pytest tests/test_delivery_scripts.py -v
```

Expected: PASS.

---

### Task 4: Propagate Metadata Through Server Agent

**Files:**
- Modify: `server_agent.py`
- Test: `tests/test_server_agent_drl_metadata.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_server_agent_drl_metadata.py`:

```python
import networkx as nx

from server_agent import ServerAgent


def test_normalize_drl_decision_preserves_metadata():
    agent = ServerAgent.__new__(ServerAgent)
    graph = nx.DiGraph()
    graph.add_edge("10.0.0.1", 1)
    graph.add_edge(1, 2)
    graph.add_edge(2, "10.0.0.2")
    agent.G = graph

    raw_response = {
        "path": [1, 2],
        "decision_source": "drl_model",
        "model_used": True,
        "fallback_reason": None,
        "confidence": 0.9,
        "compute_time": 0.02,
    }

    result = agent._normalize_drl_decision(raw_response, "10.0.0.1", "10.0.0.2")

    assert result["path"] == ["10.0.0.1", 1, 2, "10.0.0.2"]
    assert result["decision_source"] == "drl_model"
    assert result["model_used"] is True
    assert result["fallback_reason"] is None
    assert result["confidence"] == 0.9
    assert result["compute_time"] == 0.02
```

Then finish this test after Task 4 Step 3 adds a helper that can be tested without opening sockets.

- [ ] **Step 2: Add a helper to normalize DRL responses**

In `server_agent.py`, add:

```python
def _normalize_drl_decision(self, response, src_ip, dst_ip):
    if not response or not response.get("path"):
        return None
    full_path = [src_ip] + response["path"] + [dst_ip]
    valid, reason = validate_switch_path(self.G, full_path)
    if not valid:
        logger.warning("[DRL] invalid path from path_service (%s): %s", reason, full_path)
        return None
    return {
        "path": full_path,
        "decision_source": response.get("decision_source", "path_service_unknown"),
        "model_used": bool(response.get("model_used", False)),
        "fallback_reason": response.get("fallback_reason"),
        "confidence": response.get("confidence"),
        "compute_time": response.get("compute_time"),
    }
```

- [ ] **Step 3: Change `_request_path_from_drl()` to return normalized decision**

Replace:

```python
return full_path
```

with:

```python
return self._normalize_drl_decision(response, src_ip, dst_ip)
```

Rename local variables as needed so `_request_path_from_drl()` returns a decision dictionary, not only a path list.

- [ ] **Step 4: Change `handle_path_request()` to preserve metadata**

Replace:

```python
drl_path = self._request_path_from_drl(message)
if drl_path:
```

with:

```python
drl_decision = self._request_path_from_drl(message)
if drl_decision:
    drl_path = drl_decision["path"]
```

Add these fields to the DRL response:

```python
"path_source": drl_decision.get("decision_source", "path_service_unknown"),
"decision_source": drl_decision.get("decision_source", "path_service_unknown"),
"model_used": drl_decision.get("model_used", False),
"fallback_reason": drl_decision.get("fallback_reason"),
"model_confidence": drl_decision.get("confidence"),
"drl_compute_time": drl_decision.get("compute_time"),
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_server_agent_drl_metadata.py -v
pytest tests/test_delivery_scripts.py -v
```

Expected: PASS after completing the helper test.

---

### Task 5: Add Shadow Mode

**Files:**
- Modify: `server_agent.py`
- Modify: `common_config.py`
- Test: `tests/test_server_agent_shadow_mode.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_server_agent_shadow_mode.py`:

```python
from server_agent import ServerAgent


def test_shadow_mode_installs_fallback_but_records_drl_advice():
    agent = ServerAgent.__new__(ServerAgent)
    fallback = {"status": "ok", "path": ["h1", 1, 2, "h2"], "path_source": "dijkstra"}
    drl = {"status": "ok", "path": ["h1", 1, 3, 2, "h2"], "decision_source": "drl_model", "model_used": True}

    result = agent._choose_final_path_response({}, drl, fallback, "shadow")

    assert result["path"] == ["h1", 1, 2, "h2"]
    assert result["path_source"] == "shadow_fallback"
    assert result["drl_shadow"]["path"] == ["h1", 1, 3, 2, "h2"]
    assert result["drl_shadow"]["model_used"] is True
```

- [ ] **Step 2: Add response chooser helper**

In `server_agent.py`, add:

```python
def _choose_final_path_response(self, message, drl_response, fallback_response, route_mode):
    if route_mode == "spf":
        return fallback_response

    if route_mode == "shadow":
        if fallback_response.get("status") == "ok":
            fallback_response["path_source"] = "shadow_fallback"
            fallback_response["drl_shadow"] = {
                "path": drl_response.get("path") if drl_response else None,
                "decision_source": drl_response.get("decision_source") if drl_response else None,
                "model_used": drl_response.get("model_used") if drl_response else False,
                "fallback_reason": drl_response.get("fallback_reason") if drl_response else "no_drl_response",
            }
        return fallback_response

    if route_mode == "hybrid" and drl_response:
        return drl_response

    if route_mode == "drl":
        return drl_response or {"status": "error", "message": "DRL route mode requested but no valid DRL path"}

    return fallback_response
```

- [ ] **Step 3: Use helper in `handle_path_request()`**

Compute fallback response even when DRL succeeds if `DRL_ROUTE_MODE == "shadow"`. In `hybrid`, use DRL when valid. In `spf`, skip DRL or ignore it.

- [ ] **Step 4: Run the helper test once implementation exists**

Run:

```bash
pytest tests/test_server_agent_shadow_mode.py -v
```

Expected: PASS.

- [ ] **Step 5: Run tests with existing delivery checks**

Run:

```bash
pytest tests/test_server_agent_shadow_mode.py -v
```

Expected: PASS.

---

### Task 6: Add Candidate Paths To Path Service Requests

**Files:**
- Modify: `server_agent.py`
- Modify: `drl-or-s/path_service.py`
- Test: `tests/test_path_service_request_payload.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_path_service_request_payload.py`:

```python
import networkx as nx

from server_path_service import build_k_shortest_candidates


def test_candidate_payload_contains_switch_paths_and_metrics():
    graph = nx.DiGraph()
    graph.add_edge("h1", 1, edge_type="host_link")
    graph.add_edge(1, 2, edge_type="switch_link", delay=1, bw=10, loss=0.01)
    graph.add_edge(2, "h2", edge_type="host_link")

    candidates = build_k_shortest_candidates(graph, "h1", "h2", k=1)

    assert candidates[0]["switch_path"] == [1, 2]
    assert candidates[0]["metrics"]["delay"] == 1
    assert candidates[0]["metrics"]["min_bandwidth"] == 10
```

- [ ] **Step 2: Add candidates to `_request_path_from_drl()` payload**

In `server_agent.py`, import `DRL_K_CANDIDATES` from `common_config` and `build_k_shortest_candidates` from `server_path_service`.

Add to the request:

```python
"candidates": build_k_shortest_candidates(
    self.G,
    src_ip,
    dst_ip,
    k=DRL_K_CANDIDATES,
    link_down_set=self.link_down_set,
    route_policy=route_policy,
),
```

- [ ] **Step 3: Accept candidates in path_service**

In `drl-or-s/path_service.py`, read:

```python
candidates = request.get("candidates") or []
```

For now, keep existing DRL path generation. Store candidate count in response:

```python
"candidate_count": len(candidates),
```

This prepares the wire protocol without changing the model yet.

- [ ] **Step 4: Run tests**

Run:

```bash
pytest tests/test_path_service_request_payload.py -v
pytest tests/test_server_path_service_candidates.py -v
```

Expected: PASS.

---

### Task 7: Record Route Decision Metadata In Controller Sessions

**Files:**
- Modify: `controller.py`
- Modify: `web_api.py`
- Test: manual API smoke test

- [ ] **Step 1: Store metadata when handling path responses**

In `controller.py`, around the existing `_record_route_session()` call after path installation, add metadata:

```python
'path_source': msg.get('path_source', msg.get('decision_source', 'unknown')),
'decision_source': msg.get('decision_source', msg.get('path_source', 'unknown')),
'model_used': msg.get('model_used', False),
'fallback_reason': msg.get('fallback_reason'),
'model_confidence': msg.get('model_confidence'),
'drl_compute_time': msg.get('drl_compute_time'),
'drl_shadow': msg.get('drl_shadow'),
```

- [ ] **Step 2: Include metadata in topology reports**

In the `route_sessions_info.append()` payload inside `controller.py`, add:

```python
'path_source': session.get('path_source', 'unknown'),
'decision_source': session.get('decision_source', session.get('path_source', 'unknown')),
'model_used': session.get('model_used', False),
'fallback_reason': session.get('fallback_reason'),
'model_confidence': session.get('model_confidence'),
'drl_compute_time': session.get('drl_compute_time'),
'drl_shadow': session.get('drl_shadow'),
```

- [ ] **Step 3: Return metadata from Web API**

In `web_api.py` `/api/route_sessions`, include the same fields in each session response.

- [ ] **Step 4: Manual smoke test**

Run the suite on Linux/Mininet:

```bash
./start_suite.sh
```

In Mininet:

```bash
h7 ping -c 3 h35
```

Then:

```bash
curl http://localhost:6009/api/route_sessions
```

Expected: each route session includes `path_source`, `decision_source`, `model_used`, and `fallback_reason`.

---

### Task 8: Display DRL Decision State In Web UI

**Files:**
- Modify: `web_ui_html.py`
- Test: manual browser smoke test

- [ ] **Step 1: Locate route session rendering**

Search:

```bash
rg -n "route_sessions|route_policy|switch_path" web_ui_html.py
```

- [ ] **Step 2: Add compact decision fields**

In each route session row/card, display:

```javascript
const source = item.decision_source || item.path_source || 'unknown';
const modelUsed = item.model_used ? 'model' : 'fallback';
const reason = item.fallback_reason || '';
```

Render as compact text near `route_policy`:

```javascript
`${source} / ${modelUsed}${reason ? ' / ' + reason : ''}`
```

- [ ] **Step 3: Manual browser smoke test**

Start:

```bash
./start_suite.sh
```

Open:

```text
http://localhost:6009
```

Expected: route sessions show whether DRL model inference was used or fallback was used.

---

### Task 9: Rollout And Verification

**Files:**
- Modify: `RUN_TESTING_CN.md`
- Test: full suite smoke test

- [ ] **Step 1: Document route modes**

In `RUN_TESTING_CN.md`, add a short section after DRL fallback testing:

```markdown
### DRL 路由模式

可通过环境变量控制 DRL 使用方式：

- `DRL_ROUTE_MODE=shadow`：默认推荐。DRL 旁路给出建议，实际仍下发 fallback 路径。
- `DRL_ROUTE_MODE=hybrid`：DRL 路径通过校验后下发，失败回退 Dijkstra。
- `DRL_ROUTE_MODE=spf`：只使用 Dijkstra/最短路径。
- `DRL_ROUTE_MODE=drl`：强制 DRL，仅用于实验。

建议上线顺序：先 `shadow`，确认 `model_used=true` 的路径稳定后，再切换到 `hybrid`。
```

- [ ] **Step 2: Run unit tests**

Run:

```bash
pytest tests -v
```

Expected: PASS.

- [ ] **Step 3: Run shadow smoke test**

Run:

```bash
DRL_ROUTE_MODE=shadow ./start_suite.sh
```

In Mininet:

```bash
h7 ping -c 3 h35
```

Expected:

- ping succeeds.
- `/api/route_sessions` shows fallback path installed.
- route session contains `drl_shadow`.

- [ ] **Step 4: Run hybrid smoke test**

Run:

```bash
DRL_ROUTE_MODE=hybrid ./start_suite.sh
```

In Mininet:

```bash
h7 ping -c 3 h35
```

Expected:

- ping succeeds.
- if model inference succeeds and path validates, route source is `drl_model`.
- if model fails, route source is `dijkstra` or fallback source and ping still succeeds.

---

## Acceptance Criteria

- DRL path_service remains in the online path-planning loop.
- Route sessions truthfully show whether the model actually ran.
- DRL failure, invalid output, model absence, and path_service outage all fall back to Dijkstra/KSP.
- `shadow` mode can compare DRL advice against installed fallback paths.
- `hybrid` mode can install validated DRL paths without losing Dijkstra fallback.
- Existing manual flow APIs and Web topology views continue working.

## Self-Review Notes

- No forced pure-DRL rollout is required.
- The first implementation keeps current model behavior and only makes metadata and fallback truthful.
- KSP candidate payload is introduced before retraining a selector model, so the system can collect data for the future selector.
- The helper tests define the exact helper functions that must be introduced before they pass.
