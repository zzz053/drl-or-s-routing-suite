"""
该文件从原始 server_agent 大文件中拆分了“路径权重计算策略”功能，
用于在路径计算阶段按策略统一计算边权重。

函数作用：
- compute_edge_weight(route_policy, edge_data)：
  根据策略名（如 min_delay / max_bandwidth / min_loss / hybrid）与边属性
  （delay/bw/loss/edge_type）返回路径搜索使用的权重值。
"""

def compute_edge_weight(route_policy, edge_data):
    """
    统一的路径权重计算入口，便于后续扩展不同业务策略。
    当前默认行为与原逻辑兼容。
    """
    delay = edge_data.get('delay', 1)
    bw = edge_data.get('bw', 1)
    loss = edge_data.get('loss', 0)
    edge_type = edge_data.get('edge_type')

    # 非交换机链路（控制器连线、主机连线等）保持较小稳定代价
    if edge_type != 'switch_link':
        return float(edge_data.get('weight', 1))

    if route_policy == 'min_delay':
        return max(float(delay), 0.0001)
    if route_policy == 'max_bandwidth':
        return 1.0 / max(float(bw), 0.0001)
    if route_policy == 'min_loss':
        return max(float(loss), 0.0) + 0.0001
    if route_policy == 'hybrid':
        return max(float(delay), 0.0) * (1.0 + max(float(loss), 0.0)) / max(float(bw), 0.0001)

    # 默认与现有逻辑兼容
    return float(edge_data.get('weight', 1))
