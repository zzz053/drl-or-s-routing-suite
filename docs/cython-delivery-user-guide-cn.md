# DRL-OR-S Cython 混合交付落地手册

本文面向第一次接触本项目的交付、测试和现场使用人员。按顺序执行即可完成 Cython 保护包的构建、安装、启动、健康检查、报告生成和排障。

## 1. 方案目标

交付时保护核心 Python 代码，避免现场人员直接浏览控制器、server agent、path service 和 DRL 推理算法源码。

本方案采用“核心编译、非核心保留源码”：

- 核心模块编译成 Linux 原生扩展 `.so`。
- `controller.py`、`server_agent.py`、`drl-or-s/path_service.py` 只保留很薄的 loader。
- 运维脚本、验收脚本、配置、模型、拓扑和报告工具继续保留源码，便于现场排障。
- 目标平台固定为 Linux x86_64、Python 3.8.20。

边界说明：Cython 不是绝对防逆向。它比 PyInstaller 更难直接还原源码，也不依赖 PyArmor license，但 root 用户仍可复制、调试和逆向二进制产物。不要把密钥、私钥、训练原始数据放进交付包。

## 2. 交付包里有什么

核心保护内容：

- `/opt/drl-ors/controller_core*.so`
- `/opt/drl-ors/server_agent_core*.so`
- `/opt/drl-ors/drl-or-s/path_service_core*.so`
- `/opt/drl-ors/server_path_service*.so`
- `/opt/drl-ors/routing_policy*.so`
- `/opt/drl-ors/network_metrics*.so`
- `/opt/drl-ors/controller_helpers*.so`
- `/opt/drl-ors/packetin_*.so`
- `/opt/drl-ors/external_host_guard*.so`
- `/opt/drl-ors/hybrid_gateway*.so`
- `/opt/drl-ors/common_config*.so`
- `/opt/drl-ors/drl-or-s/a2c_ppo_acktr/**/*.so`
- `/opt/drl-ors/drl-or-s/net_env/**/*.so`

保留源码内容：

- `/opt/drl-ors/acceptance.sh`
- `/usr/local/bin/drl-orsctl`
- `/opt/drl-ors/tools/acceptance_config.py`
- `/opt/drl-ors/tools/acceptance_health.py`
- `/opt/drl-ors/tools/generate_acceptance_report.py`
- `/opt/drl-ors/tools/mininet_load_test.py`
- `/opt/drl-ors/testbed/creat_test_topo.py`
- `/etc/drl-ors/config.json`
- DRL 模型和拓扑数据

## 3. VM 前置条件

当前已确认的 VM：

```text
用户：hydrate
地址：192.168.172.128
项目路径：/home/hydrate/a/drl-or-s-routing-suite
conda wrapper：/home/hydrate/run_drl_ors_conda.sh
Python：3.8.20
```

从本机免密登录：

```bash
ssh hydrate@192.168.172.128
```

所有 VM 内项目命令都通过 wrapper 执行：

```bash
cd /home/hydrate/a/drl-or-s-routing-suite
/home/hydrate/run_drl_ors_conda.sh python --version
```

安装构建依赖：

```bash
cd /home/hydrate/a/drl-or-s-routing-suite
/home/hydrate/run_drl_ors_conda.sh python -m pip install "Cython>=0.29,<4" "setuptools>=61" wheel
```

## 4. 本地基础检查

在开发机仓库目录执行：

```bash
python -m pytest -q
python tools/acceptance_feature_audit.py
python tools/run_acceptance_verification.py --local-only
```

说明：Windows 本地只做交付布局烟测，不生成 Linux `.so`。正式交付包必须在 Linux VM 上生成。

## 5. 在 VM 上生成 Cython 交付包

```bash
ssh hydrate@192.168.172.128
cd /home/hydrate/a/drl-or-s-routing-suite

/home/hydrate/run_drl_ors_conda.sh python tools/build_delivery_package.py \
  --output dist/drl-ors-runtime-cython \
  --zip dist/drl-ors-runtime-cython.zip \
  --protection cython
```

生成后应看到：

```text
dist/drl-ors-runtime-cython/
dist/drl-ors-runtime-cython.zip
```

检查核心源码没有泄露：

```bash
find dist/drl-ors-runtime-cython/opt/drl-ors -name 'controller_core.py' -o -name 'server_agent_core.py' -o -name 'path_service_core.py'
find dist/drl-ors-runtime-cython/opt/drl-ors -name '*.so' | sort | head -40
sed -n '1,40p' dist/drl-ors-runtime-cython/opt/drl-ors/controller.py
sed -n '1,40p' dist/drl-ors-runtime-cython/opt/drl-ors/server_agent.py
sed -n '1,50p' dist/drl-ors-runtime-cython/opt/drl-ors/drl-or-s/path_service.py
```

正常情况：

- 第一条命令无输出。
- 能看到多个 `.so` 文件。
- 三个入口 `.py` 只包含 import loader 和 `main()` 转发，不包含大段业务逻辑。

## 6. 安装到目标路径

在 VM 上测试安装：

```bash
cd /home/hydrate/a/drl-or-s-routing-suite
rm -rf /tmp/drl-ors-install
mkdir -p /tmp/drl-ors-install
unzip -q dist/drl-ors-runtime-cython.zip -d /tmp/drl-ors-install
```

正式安装到系统路径需要 sudo：

```bash
sudo rsync -a /tmp/drl-ors-install/opt/ /opt/
sudo rsync -a /tmp/drl-ors-install/etc/ /etc/
sudo rsync -a /tmp/drl-ors-install/usr/ /usr/
sudo rsync -a /tmp/drl-ors-install/var/ /var/
sudo chmod +x /usr/local/bin/drl-orsctl /opt/drl-ors/acceptance.sh
```

现场只改配置：

```bash
sudo nano /etc/drl-ors/config.json
```

不要在 `/opt/drl-ors` 里直接改核心逻辑；核心逻辑要回开发仓库修改后重新构建。

## 7. 启动、停止、健康检查、报告

`drl-orsctl` 会自动设置：

```bash
PYTHONPATH=/opt/drl-ors:$PYTHONPATH
ACCEPTANCE_CONFIG=/etc/drl-ors/config.json
```

常用命令：

```bash
sudo -E drl-orsctl stop
sudo -E drl-orsctl start
sudo -E drl-orsctl health
sudo -E drl-orsctl report
```

如果当前环境 sudo 需要密码，可以先导出：

```bash
export SUDO_PASSWORD='h'
sudo -E drl-orsctl start
```

报告目录：

```text
/var/lib/drl-ors/reports
```

日志目录：

```text
/var/log/drl-ors
```

## 8. 开发仓库内验收

不安装交付包时，也可以直接在开发仓库验收：

```bash
cd /home/hydrate/a/drl-or-s-routing-suite
export SUDO_PASSWORD='h'
/home/hydrate/run_drl_ors_conda.sh ./acceptance.sh stop
/home/hydrate/run_drl_ors_conda.sh ./acceptance.sh start
/home/hydrate/run_drl_ors_conda.sh ./acceptance.sh health
/home/hydrate/run_drl_ors_conda.sh ./acceptance.sh report
```

在 VM 上不要直接裸跑 `./acceptance.sh`，统一使用 `/home/hydrate/run_drl_ors_conda.sh`。

## 9. 常见故障

### 9.1 找不到 Cython

现象：

```text
ModuleNotFoundError: No module named 'Cython'
```

处理：

```bash
/home/hydrate/run_drl_ors_conda.sh python -m pip install "Cython>=0.29,<4" "setuptools>=61" wheel
```

### 9.2 `.so` 无法导入

常见原因：

- 不是在目标 Linux VM 上构建。
- 构建 Python 版本和运行 Python 版本不一致。
- `PYTHONPATH` 没有包含 `/opt/drl-ors`。

检查：

```bash
python --version
cat /opt/drl-ors/../PYTHON_RUNTIME.txt 2>/dev/null || true
python - <<'PY'
import sys
print(sys.path[:5])
import controller_core
print(controller_core)
PY
```

### 9.3 健康检查失败

先看日志：

```bash
sudo tail -n 120 /var/log/drl-ors/path_service.log
sudo tail -n 120 /var/log/drl-ors/server_agent.stdout.log
sudo tail -n 120 /var/log/drl-ors/controllers.log
sudo tail -n 120 /var/log/drl-ors/mininet_topology.log
```

再确认配置：

```bash
python /opt/drl-ors/tools/acceptance_config.py --config /etc/drl-ors/config.json --shell-env
```

### 9.4 需要重新交付

开发仓库改源码后重新跑：

```bash
cd /home/hydrate/a/drl-or-s-routing-suite
/home/hydrate/run_drl_ors_conda.sh python -m pytest -q
/home/hydrate/run_drl_ors_conda.sh python tools/acceptance_feature_audit.py
/home/hydrate/run_drl_ors_conda.sh python tools/build_delivery_package.py \
  --output dist/drl-ors-runtime-cython \
  --zip dist/drl-ors-runtime-cython.zip \
  --protection cython
```

## 10. 通过标准

交付可用需要同时满足：

- VM 上成功生成 `dist/drl-ors-runtime-cython.zip`。
- 交付包内存在核心 `.so`。
- 交付包内不存在核心源码副本。
- `drl-orsctl start` 成功。
- `drl-orsctl health` 通过。
- `drl-orsctl report` 能生成报告。
- `/etc/drl-ors/config.json` 可读、可改、可用于现场排障。
