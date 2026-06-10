# VM 验收部署说明

当前 VM 验收流程请先阅读主手册：

```text
docs/cython-delivery-user-guide-cn.md
```

已确认的 VM 信息：

- 用户：`hydrate`
- 地址：`192.168.172.128`
- 仓库路径：`/home/hydrate/a/drl-or-s-routing-suite`
- conda wrapper：`/home/hydrate/run_drl_ors_conda.sh`

验收命令统一通过 wrapper 执行，不直接裸跑本仓库脚本。

保留的验收锚点：

- 配置文件：`config/hybrid_acceptance.json`、`config/hybrid_acceptance.vm.json`、`config/hybrid_acceptance.server.json`
- 当前 VM 数据面网卡：`ens34`
- 服务器模板网卡名可能仍是：`eno1`
- VM 边界 LLDP 可能受限，因此验收依赖“静态虚实边界”配置
- 开发仓库内命令仍是：`./acceptance.sh start`、`./acceptance.sh health`、`./acceptance.sh report`
