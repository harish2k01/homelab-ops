# 🏠 Homelab Ops

GitOps repository for my self-hosted Kubernetes homelab.

This repository is the source of truth for cluster infrastructure, platform services, observability, and self-hosted applications. Argo CD continuously reconciles the manifests in this repo into the cluster using a mix of upstream Helm charts, local Helm charts, and Kustomize-managed infrastructure.

## 📋 Overview

The cluster is managed with a declarative GitOps workflow:

1. Infrastructure and application configuration live in this repository.
2. Argo CD watches the `main` branch and syncs changes into the cluster.
3. Upstream applications are installed with Helm and repo-managed values files.
4. Custom workloads are packaged as local Helm charts.
5. Cluster resources and supporting manifests are managed with Kustomize.
6. Secrets are committed as encrypted Sealed Secrets where possible.

## 📂 Repository Layout

```text
.
|-- argocd-apps/   # Argo CD Application manifests
|-- charts/        # Helm values files and local Helm charts
|-- infra/         # Kustomize-managed cluster resources
|-- manifests/     # Standalone manifests consumed by selected apps
`-- README.md
```

## 🧰 Stack

### 🚀 GitOps and cluster platform

- Argo CD
- cert-manager
- Sealed Secrets
- Gateway API
- MetalLB
- Longhorn
- CSI Driver NFS
- CloudNativePG

### 🌐 Networking and access

- Traefik
- ingress-nginx
- cloudflared
- Tailscale

### 📊 Observability

- kube-prometheus-stack
- Grafana dashboards
- Loki
- Alloy
- Uptime Kuma
- Speedtest Tracker
- Robusta
- Scrutiny
- Proxmox exporter
- qBittorrent exporter

### 🤖 CI and automation

- GitHub Actions Runner Controller
- Runner scale sets for `homelab-ops`, `helm-charts`, and `portfolio`
- Renovate

### 📦 Applications

- AdGuard Home
- Bazarr and Bazarr 4K
- BentoPDF
- Firefly III
- FlareSolverr
- Ghost
- Headlamp
- Homepage
- Immich
- Jellyfin
- Portfolio dev/prod
- Portfolio Tracker
- Prowlarr
- qBittorrent
- Radarr and Radarr 4K
- Seerr
- SonarQube
- Sonarr and Sonarr Anime
- Tor proxy
- Umami
- Vaultwarden

## 🔄 Deployment Model

Most applications are represented by an Argo CD `Application` in `argocd-apps/`.

Upstream Helm chart deployments use Argo CD multi-source applications:

- one source points at the upstream Helm repository
- one source points at this repository as the values source
- the chart consumes the matching `charts/<app>/values.yaml`

Local workloads are deployed directly from charts in this repository, including custom charts such as:

- `charts/grafana-dashboards`
- `charts/portfolio-dev`
- `charts/portfolio-prod`
- `charts/pve-exporter`
- `charts/qbittorrent-exporter`

Infrastructure resources are reconciled through the `infra` Argo CD application, which points at the `infra/` Kustomize root.

## 🏗️ Infrastructure

The `infra/` tree contains cluster-level resources and supporting app manifests, including:

- Argo CD HTTPRoute resources
- cert-manager issuers and wildcard certificates
- Longhorn routes and storage class configuration
- MetalLB address pool configuration
- Traefik Gateway API resources
- monitoring routes, scrape configs, and alerting secrets
- GitHub Actions Runner Controller secrets and supporting resources
- app-specific persistent volumes, routes, and sealed secrets

App-specific and platform-specific overlays are attached to their matching
Argo CD applications as additional sources. The root kustomization only keeps
shared resources that are not owned by a single app.

The root kustomization currently includes:

```text
infra/
`-- media/
```

## ⚡ Bootstrap

Prerequisites:

- a working Kubernetes cluster
- `kubectl`
- `helm`
- `kustomize`
- access to install controllers, CRDs, and cluster-scoped resources
- Argo CD installed or a plan to bootstrap it manually first

Clone the repository:

```bash
git clone https://github.com/harish2k01/homelab-ops.git
cd homelab-ops
```

After Argo CD is available in the cluster, apply the app definitions:

```bash
kubectl apply -f argocd-apps/
```

Argo CD will then reconcile the declared applications and infrastructure from Git.

## ✅ Local Validation

Render the Kustomize infrastructure root:

```bash
kustomize build infra/
```

Render local Helm charts:

```bash
helm template grafana-dashboards charts/grafana-dashboards
helm template portfolio-prod charts/portfolio-prod
helm template pve-exporter charts/pve-exporter
helm template qbittorrent-exporter charts/qbittorrent-exporter
```

Inspect an upstream-chart values file before syncing:

```bash
helm show values <repo>/<chart>
```

## 🔐 Secrets

Sensitive values should not be committed as plain Kubernetes `Secret` manifests. Use Sealed Secrets for cluster-bound secrets:

1. Create a normal Kubernetes Secret manifest locally.
2. Seal it with the cluster's Sealed Secrets public certificate.
3. Commit the sealed manifest to Git.
4. Let Argo CD sync it into the cluster.

Example workflow:

```bash
kubeseal --format yaml --cert sealed-secrets.pem < secret.yaml > sealed-secret.yaml
```

## 📝 Operating Notes

- Argo CD automated sync is enabled for the app manifests in this repo.
- Most apps use `CreateNamespace=true` so namespaces can be created during sync.
- Gateway API `HTTPRoute` resources are primarily routed through the Traefik Gateway.
- TLS is managed with cert-manager.
- MetalLB provides LoadBalancer addresses on the homelab network.
- Grafana dashboards are versioned in Git and deployed through the `grafana-dashboards` chart.
- Renovate tracks dependency updates for charts and container images.

## 🎯 Purpose

This repo documents and operates a Kubernetes-based homelab in a reproducible way. It also serves as a practical DevOps portfolio project covering GitOps, Helm, Kustomize, ingress, storage, secrets, observability, automation, and self-hosted application delivery.
