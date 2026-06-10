# DRL-OR-S Routing Suite

This directory is the self-contained deliverable package for the DRL-OR-S routing system.

It keeps operational scripts, config, model weights, topology data, and runtime
helpers readable, while compiling core Python logic into Linux native
extensions with Cython.

## Ports

- server socket: `6001`
- Web UI: `6009`
- DRL path service: `8889`

## Start in development tree

```bash
pip3 install -r requirements.txt
./start_suite.sh
```

Hybrid physical attachment example:

```bash
./start_suite.sh <external-data-plane-nic>
```

## Cython delivery package

Build the protected runtime package:

```bash
python tools/build_delivery_package.py --output dist/drl-ors-runtime-cython --protection cython
```

Linux delivery reference:

- [中文落地手册](docs/cython-delivery-user-guide-cn.md)
- Old entry points now redirect to the Cython manual.

## Acceptance

Use the Military topology:

```bash
sudo python3 testbed/creat_test_topo.py
```

For VM acceptance, use `/home/hydrate/run_drl_ors_conda.sh` to run all
commands.
