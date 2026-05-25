# Military Topology for DRL-OR-S

## Topology Structure

**47 nodes** organized in 3 layers:

### Core Layer (9 nodes)
- **Outer ring**: Nodes 1-6 (backbone routers in hexagonal ring)
- **Inner ring**: Nodes 45-47 (core routers)

### Access Layer (6 subnets)
1. **Brigade HQ**: Nodes 42-44 (3 nodes)
2. **Combat Squad 1**: Nodes 7-13 (7 nodes)
3. **Combat Squad 2**: Nodes 14-20 (7 nodes)
4. **Combat Squad 3**: Nodes 21-27 (7 nodes)
5. **Combat Squad 4**: Nodes 28-34 (7 nodes)
6. **Combat Squad 5**: Nodes 35-41 (7 nodes)

## Links (60 total)

### Backbone Links (18 links)
- **Outer ring**: 1-2, 2-3, 3-4, 4-5, 5-6, 6-1 (6 links)
- **Outer to inner**: 1-45, 1-47, 2-45, 2-46, 3-45, 3-46, 4-46, 5-46, 5-47, 6-47 (10 links)
- **Inner ring**: 45-47, 46-47 (2 links)
- **Bandwidth**: 9920 Kbps, Weight: 5.0

### Access Links (12 links)
- Brigade HQ: 2-42, 3-42
- Squad 1: 3-7, 4-7
- Squad 2: 4-14, 5-14
- Squad 3: 5-21, 6-21
- Squad 4: 6-28, 1-28
- Squad 5: 1-35, 2-35
- **Bandwidth**: 5000 Kbps, Weight: 8.0

### Subnet Internal Links (30 links)
Each subnet has internal tree/star topology
- **Bandwidth**: 3000 Kbps, Weight: 3.0

## Traffic Matrix

47×47 matrix (2209 values) with differentiated traffic patterns:
- Backbone-to-backbone: 250 Kbps
- Backbone-to-Brigade HQ: 450 Kbps (high priority)
- Backbone-to-squads: 350 Kbps
- Brigade HQ internal: 400 Kbps
- Squad-to-squad: 150 Kbps

## Training Configuration

In [drl-or-s/net_env/simenv.py](../drl-or-s/net_env/simenv.py):
- Request demands: [100, 1500, 1500, 500] Kbps
- Request times: [100, 100, 100, 100] (mid load)

## Usage

### 1. Start NN-Simulator
```bash
cd NN-simulator
python3 main.py --mode simulator --topo Military --log-dir logs/Military --simu-port 5010
```

### 2. Train DRL-OR-S
```bash
cd drl-or-s
python3 main.py --mode train --env-name Military --log-dir ./log/Military \
  --model-save-path ./model/Military --simu-port 5010 --num-env-steps 3000000
```

### 3. Test Model
```bash
cd drl-or-s
python3 main.py --mode test --env-name Military --model-load-path ./model/Military \
  --log-dir ./log/Military_test --simu-port 5010
```

## Files

- [Topology.txt](Topology.txt): Network structure (47 nodes, 60 links)
- [TM.txt](TM.txt): Traffic matrix (47×47 = 2209 values)
- [link_weight.json](link_weight.json): Link weights (120 values for 60 bidirectional links)
