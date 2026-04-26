# Kubernetes Cluster Architecture

## Overview

A **5-node Talos Linux Kubernetes cluster** distributed across two Proxmox hypervisors (PVE and PVE2) for high availability and redundancy.

---

## 🏗️ Infrastructure Layout

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROXMOX INFRASTRUCTURE                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────┐    ┌──────────────────────────┐   │
│  │      PVE (Host 1)        │    │     PVE2 (Host 2)        │   │
│  ├──────────────────────────┤    ├──────────────────────────┤   │
│  │                          │    │                          │   │
│  │  ┌────────────────────┐  │    │  ┌────────────────────┐  │   │
│  │  │   vm-kubecp        │  │    │  │   vm-knode1        │  │   │
│  │  │ (Control Plane)    │  │    │  │ (Worker Node)      │  │   │
│  │  │  zone: pve         │  │    │  │  zone: pve2        │  │   │
│  │  └────────────────────┘  │    │  └────────────────────┘  │   │
│  │                          │    │                          │   │
│  │  ┌────────────────────┐  │    │  ┌────────────────────┐  │   │
│  │  │   vm-knode2        │  │    │  │   vm-knode4        │  │   │
│  │  │ (Worker Node)      │  │    │  │ (Worker Node)      │  │   │
│  │  │  zone: pve         │  │    │  │  zone: pve2        │  │   │
│  │  └────────────────────┘  │    │  └────────────────────┘  │   │
│  │                          │    │                          │   │
│  │  ┌────────────────────┐  │    │                          │   │
│  │  │   vm-knode3        │  │    │                          │   │
│  │  │ (Worker Node)      │  │    │                          │   │
│  │  │  zone: pve         │  │    │                          │   │
│  │  └────────────────────┘  │    │                          │   │
│  │                          │    │                          │   │
│  │  ┌────────────────────┐  │    │                          │   │
│  │  │  NFS Storage LXC   │  │    │                          │   │
│  │  │  (External)        │  │    │                          │   │
│  │  └────────────────────┘  │    │                          │   │
│  │                          │    │                          │   │
│  └──────────────────────────┘    └──────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📊 Node Distribution

### Control Plane
| Node | Host | Zone | Role |
|------|------|------|------|
| vm-kubecp | PVE | pve | Kubernetes Control Plane |

### Workers
| Node | Host | Zone | Role |
|------|------|------|------|
| vm-knode1 | PVE2 | pve2 | Worker |
| vm-knode2 | PVE | pve | Worker |
| vm-knode3 | PVE | pve | Worker |
| vm-knode4 | PVE2 | pve2 | Worker |

### Storage
| Component | Host | Type | Purpose |
|-----------|------|------|---------|
| NFS Storage LXC | PVE | LXC Container | Shared storage for Kubernetes services |

---

## 🏷️ Node Labels

All nodes are labeled with topology information for scheduling:

### Zone Label (Standard Kubernetes)
```
topology.kubernetes.io/zone=pve   # PVE nodes
topology.kubernetes.io/zone=pve2  # PVE2 nodes
```

### Host Label (Custom)
```
host=pve   # PVE nodes
host=pve2  # PVE2 nodes
```

**Applied to:**
- vm-kubecp: `zone=pve, host=pve`
- vm-knode1: `zone=pve2, host=pve2`
- vm-knode2: `zone=pve, host=pve`
- vm-knode3: `zone=pve, host=pve`
- vm-knode4: `zone=pve2, host=pve2`

---

## 🔄 Network & Connectivity

```
PVE Cluster Network:
  PVE ←→ vm-kubecp (10.10.10.220)
  PVE ←→ vm-knode2 (10.10.10.222)
  PVE ←→ vm-knode3 (10.10.10.223)
  PVE ←→ NFS Storage LXC (10.10.10.253)

PVE2 Cluster Network:
  PVE2 ←→ vm-knode1 (10.10.10.221)
  PVE2 ←→ vm-knode4 (10.10.10.224)

Kubernetes Pod Network:
  Overlay network spanning all nodes
  CNI (Cilium/Calico/Weave) handles cross-host communication
```

---

## 📦 Storage Architecture

### NFS Storage (PVE)
- **Location:** LXC container on PVE hypervisor
- **Purpose:** Shared persistent storage for Kubernetes workloads
- **Access:** 
  - Primary access from PVE nodes (vm-kubecp, vm-knode2, vm-knode3)
  - Secondary access from PVE2 nodes via network

### Storage Options in Cluster
| Storage Type | Use Case | Nodes |
|--------------|----------|-------|
| Longhorn | Distributed block storage | All nodes |
| NFS CSI | Network file system | Prefer PVE, available on all |
| Local Storage | Node-local ephemeral | Individual nodes |

---

## 🔐 Availability & Redundancy

### Control Plane
- **Nodes:** 1 (vm-kubecp on PVE)
- **Impact:** Single point of failure
- **Mitigation:** etcd backups, quick recovery via Talos

### Worker Distribution
- **PVE:** 2 workers (vm-knode2, vm-knode3) + Control Plane
- **PVE2:** 2 workers (vm-knode1, vm-knode4)
- **Ratio:** 60% PVE / 40% PVE2

### Zone Failure Scenarios

#### PVE Host Down (3 nodes lost)
```
Remaining: vm-knode1, vm-knode4 (pve2)
Status: Degraded, control plane offline
Action: Manual intervention to restore control plane
```

#### PVE2 Host Down (2 nodes lost)
```
Remaining: vm-kubecp, vm-knode2, vm-knode3 (pve)
Status: Operational, control plane alive
Impact: Zone-spread workloads run on PVE (1 pod instead of 2)
```

---

## 📈 Scaling Considerations

### Current Capacity
- **Total CPU cores:** Based on VM sizing
- **Total Memory:** Based on VM sizing
- **Storage:** Limited by NFS LXC capacity

### Horizontal Scaling Options
1. **Add more workers to PVE2** (recommended for balance)
2. **Add more workers to PVE** (increases control plane strain)
3. **Migrate control plane to dedicated node** (future improvement)

### Recommended Future Architecture
```
Control Plane Only:
  - vm-kubecp-only (dedicated CP node)

PVE Workers:
  - vm-knode2, vm-knode3, vm-knode5, vm-knode6...

PVE2 Workers:
  - vm-knode1, vm-knode4, vm-knode7, vm-knode8...
```

---

## 🔗 Network Topology Map

```
┌──────────────────────────────────────────────────────┐
│         Kubernetes Service Network                   │
│         (Internal cluster communication)             │
└──────────────────────────────────────────────────────┘
            ↑       ↑       ↑       ↑       ↑
            │       │       │       │       │
    ┌───────┴───────┴───────┴───────┴───────┴───────┐
    │     Kubernetes Pod Network (Overlay CNI)      │
    │     Span across PVE and PVE2                  │
    └──────┬──────────────────────────────┬─────────┘
           │                              │
    ┌──────┴──────────┐          ┌────────┴─────────┐
    │                 │          │                  │
    │   PVE Nodes     │          │   PVE2 Nodes     │
    │ Cluster Network │          │ Cluster Network  │
    │                 │          │                  │
    │ vm-kubecp       │          │ vm-knode1        │
    │ vm-knode2       │          │ vm-knode4        │
    │ vm-knode3       │          │                  │
    │ NFS LXC         │          │                  │
    │                 │          │                  │
    └─────────────────┘          └──────────────────┘
```

---

## 🚀 Deployment Architecture (ArgoCD GitOps)

```
Git Repository (homelab-ops)
    ↓
ArgoCD (runs on vm-kubecp)
    ↓
├─ Infrastructure
│  ├─ Cert-Manager
│  ├─ Sealed Secrets
│  ├─ MetalLB
│  ├─ Longhorn
│  └─ CSI NFS Driver
│
├─ Networking
│  ├─ Ingress-Nginx
│  ├─ Traefik
│  └─ Gateway API
│
├─ Observability
│  ├─ Prometheus (spread across zones)
│  ├─ Grafana (spread across zones)
│  ├─ Loki
│  └─ Alloy
│
└─ Applications
   ├─ Portfolio (spread across zones)
   ├─ Headlamp
   └─ GitHub Actions Runners
```

---

## 📋 Node Health & Monitoring

### Key Metrics to Monitor
- **Zone availability:** Both pve and pve2 zones healthy
- **Pod distribution:** Verify zone spread working
- **NFS latency:** Check for PVE2→PVE network issues
- **Control plane:** vm-kubecp responsiveness

### Health Checks
```bash
# Check all nodes and zones
kubectl get nodes -L topology.kubernetes.io/zone,host

# Check pod distribution by zone
kubectl get pods -A -o wide | grep pve

# Check NFS connectivity
kubectl get storageclass
kubectl get pvc -A
```

---

## 🎯 Summary

| Aspect | Details |
|--------|---------|
| **Total Nodes** | 5 (1 Control Plane, 4 Workers) |
| **Hosts** | 2 (PVE, PVE2) |
| **Node Distribution** | 3 on PVE, 2 on PVE2 |
| **Labels** | `topology.kubernetes.io/zone` + `host` |
| **Taints** | None (maximum flexibility) |
| **Storage** | NFS LXC on PVE + Longhorn distributed |
| **HA Strategy** | Zone spread for stateless, hard-pin for storage |
| **Failure Mode** | PVE2 down = degraded, PVE down = control plane offline |
