# Kubernetes Cluster Architecture

## Overview

A **3-node Talos Linux Kubernetes cluster** distributed across two Proxmox hypervisors (PVE and PVE2) with 1 control plane and 2 worker nodes.

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
│  │  ┌────────────────────┐  │    │                          │   │
│  │  │   vm-knode2        │  │    │                          │   │
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

---

## 🔄 Network & Connectivity

```
PVE Cluster Network:
  PVE ←→ vm-kubecp (10.10.10.220)
  PVE ←→ vm-knode2 (10.10.10.222)

PVE2 Cluster Network:
  PVE2 ←→ vm-knode1 (10.10.10.221)

Kubernetes Pod Network:
  Overlay network spanning all nodes
  CNI (Cilium/Calico/Weave) handles cross-host communication
```

---

## 📦 Storage Architecture

### Storage Options in Cluster
| Storage Type | Use Case | Nodes |
|--------------|----------|-------|
| Longhorn | Distributed block storage | All nodes |
| Local Storage | Node-local ephemeral | Individual nodes |

---

## 🔐 Availability & Redundancy

### Control Plane
- **Nodes:** 1 (vm-kubecp on PVE)
- **Impact:** Single point of failure
- **Mitigation:** etcd backups, quick recovery via Talos

### Worker Distribution
- **PVE:** 1 worker (vm-knode2) + Control Plane
- **PVE2:** 1 worker (vm-knode1)
- **Ratio:** 67% PVE / 33% PVE2

### Zone Failure Scenarios

#### PVE Host Down (2 nodes lost)
```
Remaining: vm-knode1 (pve2)
Status: Degraded, control plane offline
Action: Manual intervention to restore control plane
```

#### PVE2 Host Down (1 node lost)
```
Remaining: vm-kubecp, vm-knode2 (pve)
Status: Operational, control plane alive
Impact: Single PVE zone operational
```

---

## � Deployment Architecture (ArgoCD GitOps)

```
Git Repository (homelab-ops)
    ↓
ArgoCD (runs on vm-kubecp)
    ↓
├─ Infrastructure
│  ├─ Cert-Manager
│  ├─ Sealed Secrets
│  ├─ MetalLB
│  └─ Longhorn
│
├─ Networking
│  ├─ Ingress-Nginx
│  ├─ Traefik
│  └─ Gateway API
│
├─ Observability
│  ├─ Prometheus
│  ├─ Grafana
│  ├─ Loki
│  └─ Alloy
│
└─ Applications
   ├─ Portfolio
   ├─ Headlamp
   └─ GitHub Actions Runners
```

---

## 📋 Node Health & Monitoring

### Key Metrics to Monitor
- **Zone availability:** Both pve and pve2 zones healthy
- **Control plane:** vm-kubecp responsiveness
- **Node health:** All nodes responsive

### Health Checks
```bash
# Check all nodes and zones
kubectl get nodes -L topology.kubernetes.io/zone,host

# Check pod distribution by zone
kubectl get pods -a -o wide | grep pve

# Check storage
kubectl get storageclass
kubectl get pvc -a
```

---

## 🎯 Summary

| Aspect | Details |
|--------|---------|
| **Total Nodes** | 3 (1 Control Plane, 2 Workers) |
| **Hosts** | 2 (PVE, PVE2) |
| **Node Distribution** | 2 on PVE, 1 on PVE2 |
| **Labels** | `topology.kubernetes.io/zone` + `host` |
| **Taints** | None |
| **Storage** | Longhorn distributed |
| **HA Strategy** | Zone spread for workers |
| **Failure Mode** | PVE2 down = degraded, PVE down = control plane offline |
