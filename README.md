# 🏠 Homelab-Ops

GitOps repository for my self-hosted Kubernetes homelab.

This repo uses Argo CD to continuously reconcile infrastructure and application manifests from Git into the cluster. It combines upstream Helm charts, custom Helm charts, and Kustomize-managed infrastructure so the cluster state stays declarative, observable, and repeatable.

## 📋 Overview

The cluster is managed with a GitOps workflow:

1. Infrastructure and app configuration live in this repository.
2. Argo CD watches the repo and syncs changes automatically.
3. Helm is used for upstream charts and custom workloads.
4. Kustomize is used for cluster-level infrastructure resources.
5. Monitoring dashboards and app-specific exporters are managed alongside the platform.

## 🛠️ Current Stack

### 🎯 Core platform:

- Argo CD
- cert-manager
- Sealed Secrets
- MetalLB
- Longhorn
- Gateway API
- CSI Driver NFS

### 🌐 Networking and ingress:

- ingress-nginx
- Traefik
- cloudflared

### 📊 Observability:

- kube-prometheus-stack
- Grafana
- Grafana dashboards
- Loki
- Alloy
- pve-exporter for Proxmox metrics
- qbittorrent-exporter

### 📱 Applications and services:

- `portfolio-dev`
- `portfolio-prod`
- Headlamp
- GitHub Actions Runner Controller with runner scale sets
- Firefly III

## 📂 Repository Layout

```text
.
|-- argocd-apps/     # Argo CD Application manifests
|-- charts/          # Helm values files and custom Helm charts
|-- infra/           # Kustomize-managed infrastructure resources
`-- README.md
```

### `argocd-apps/`

Argo CD `Application` resources for bootstrapping and syncing workloads such as:

- `argocd`
- `alloy`
- `cert-manager`
- `csi-driver-nfs`
- `gateway-api`
- `grafana-dashboards`
- `headlamp`
- `gh-runner-scale-set-controller`
- `gh-runner-scale-set`
- `ingress-nginx`
- `kube-prometheus-stack`
- `loki`
- `longhorn`
- `metallb`
- `pve-exporter`
- `qbittorrent-exporter`
- `sealed-secrets`
- `traefik`
- `firefly-iii`
- `portfolio-dev`
- `portfolio-prod`
- `infra`

### `charts/`

Contains two kinds of content:

- Values files for upstream Helm charts such as Argo CD, Traefik, Longhorn, Loki, Headlamp, cert-manager, and kube-prometheus-stack
- Custom Helm charts for workloads managed directly from this repo, including `portfolio-dev`, `portfolio-prod`, `pve-exporter`, `qbittorrent-exporter`, `csi-driver-nfs`, `firefly-iii`, and `grafana-dashboards`
- Helm values for GitHub Actions Runner Controller components under `charts/actions-runner-controller/`

### `infra/`

Kustomize-managed cluster resources and supporting manifests, including:

- Argo CD ingress resources
- cert-manager issuers and certificates
- Grafana, Prometheus, and Alertmanager ingress resources
- Longhorn ingress and storage configuration
- MetalLB configuration
- Traefik Gateway API resources
- Headlamp infrastructure resources
- cloudflared manifests
- Actions Runner Controller supporting resources

## 🚀 How Deployments Work

Most third-party apps are deployed with multi-source Argo CD applications:

- Source 1: upstream Helm chart repository
- Source 2: this repository for the matching `values.yaml`

Custom apps such as `portfolio-*`, `pve-exporter`, `qbittorrent-exporter`, and `grafana-dashboards` are deployed directly from local charts in [`charts/`](https://github.com/harish2k01/homelab-ops/tree/main/charts).

Infrastructure resources under [`infra/`](https://github.com/harish2k01/homelab-ops/tree/main/infra) are applied through the `infra` Argo CD application using Kustomize.

## ⚡ Getting Started

### 📋 Prerequisites

- A working Kubernetes cluster
- `kubectl`
- `helm`
- `kustomize` for local rendering and validation
- Access to the cluster with enough permissions to install controllers and CRDs

### 🔧 Bootstrap

Clone the repo:

```bash
git clone <repository-url>
cd homelab-ops
```

Apply the Argo CD app definitions after Argo CD itself is available in the cluster:

```bash
kubectl apply -f argocd-apps/
```

If you want to inspect the raw infrastructure manifests locally:

```bash
kustomize build infra/
```

If you want to render a chart locally:

```bash
helm template grafana-dashboards charts/grafana-dashboards
helm template portfolio-prod charts/portfolio-prod
helm template pve-exporter charts/pve-exporter
```

## 💡 Notable Details

- `portfolio-dev` and `portfolio-prod` currently expose Kubernetes `Ingress` resources with the `nginx` ingress class.
- Gateway API resources are also present in the repo for Traefik-based routing.
- TLS is managed with cert-manager.
- Sensitive data is intended to be stored as Sealed Secrets instead of plain Kubernetes Secrets.
- MetalLB provides service IPs for LoadBalancer workloads in the homelab network.
- Grafana dashboards are versioned in Git and deployed through a dedicated Helm chart.
- Proxmox and qBittorrent metrics are collected through exporter workloads and surfaced in Grafana.

## 🔐 Secret Management

Secrets should be committed in encrypted form using Sealed Secrets:

1. Create a regular Kubernetes Secret manifest.
2. Seal it with the cluster's Sealed Secrets public certificate.
3. Commit the sealed manifest to Git.
4. Let Argo CD sync it into the cluster.

## ✨ Why This Repo Exists

This repository serves both as the operational source of truth for my homelab and as a practical DevOps portfolio project. It reflects how I manage cluster infrastructure, ingress, storage, observability, dashboards, and application delivery in a reproducible way.
