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
|-- .github/       # GitHub Actions workflows and GitOps preview/apply scripts
|-- argocd-apps/   # Argo CD Application manifests
|-- charts/        # Helm values files and local Helm charts
|-- infra/         # Shared Kustomize-managed cluster resources
|-- manifests/     # App-specific Kustomize and standalone manifests
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
- Node Feature Discovery and SMART exporter for physical Talos SSDs
- qBittorrent exporter

### 🤖 CI and automation

- GitHub Actions Runner Controller
- Repository-scoped runner scale sets for `homelab-ops`, `helm-charts`, `Portfolio`, `portfolio-next`, `portfolio-tracker`, `PaperVault`, `railwatch`, and `tech-bytes-playground` (Ghost theme)
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
- Portfolio dev/prod (legacy site at `v1.harish2k01.xyz`) and Portfolio Next (`harish2k01.xyz`)
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

### Automatic physical SSD monitoring

The `node-feature-discovery` Application detects hardware every minute and sets
`feature.node.kubernetes.io/cpu-model.hypervisor`. The `smartctl-exporter` local
chart selects nodes with value `none`; nodes with a detected hypervisor or no
classification are excluded. New bare-metal nodes join collection automatically,
including control-plane nodes, without updating hostnames or manually labeling
nodes. Hypervisors that hide their identity can evade hardware detection.

The exporter discovers host ATA/NVMe identifiers under `/dev/disk/by-id`, excludes
partitions, deduplicates aliases, and rechecks disks every five minutes. It
restarts its exporter process when disks are added or removed. Longhorn iSCSI
volumes are excluded; SAS/SCSI and USB bridges without ATA/NVMe identifiers are
outside this discovery policy. A node without eligible disks stays unready so
the exporter pod availability alert can report missing collection.

The local chart uses upstream `smartctl-exporter:v0.14.0` with a discovery wrapper
because the upstream Helm chart does not provide the command/volume customization
needed for physical disk discovery. Its ServiceMonitor and custom PrometheusRule
match the `kube-prometheus-stack` release label. Grafana Git Sync reads
`Infra/ssd-health.json` from the separate `grafana-dashboards` repository.

The same discovery loop caches full read-only `smartctl -x --json` reports every
five minutes with a 30-second per-device timeout. A Python sidecar without host
device access exposes all numeric report fields and text descriptions on the
internal Service port 9634. Mutable raw display strings remain in JSON rather
than changing Prometheus labels on every collection; their numeric values are
exported. The dashboard shows only essential health readings, repeated for each
node/disk pair. Failed reads, stale reports and endpoint failures have alerts.

For a complete report during troubleshooting:

```sh
kubectl -n monitoring port-forward pod/<smartctl-exporter-pod> 9634:9634
curl http://127.0.0.1:9634/reports/sda.json
```

Use the discovered device name instead of `sda` for another disk. The endpoint
has no public route; reports are ephemeral and refreshed rather than archived.
Tests for parsing, device failures, report retrieval and path handling run with
`python -B charts/smartctl-exporter/tests/test_extended.py`.

Follow the existing `olympus` Application approval workflow for both Applications.
Sync NFD first; exporter scheduling begins after its labels appear. Verify:

```sh
kubectl get nodes -L feature.node.kubernetes.io/cpu-model.hypervisor
kubectl -n monitoring get daemonset,pods,servicemonitor,prometheusrule
```

Expect one exporter per classified bare-metal node with eligible disks. Verify
Prometheus target/rule health and Grafana rendering after Git Sync. VM backing
SSD health must be collected on Proxmox. Missing SMART metrics are unavailable
telemetry. SATA wear attributes vary by vendor; unsupported readings are omitted
from the health table. The initial temperature alert is 60 C for 15 minutes; tune it to vendor
ratings. Collection does not start SMART self-tests.

Most applications are represented by an Argo CD `Application` in `argocd-apps/`.

Upstream Helm chart deployments use Argo CD multi-source applications:

- one source points at the upstream Helm repository
- one source points at this repository as the values source
- the chart consumes the matching `charts/<app>/values.yaml`

Local workloads are deployed directly from charts in this repository, including custom charts such as:

- `charts/grafana-dashboards`
- `charts/portfolio-dev`
- `charts/portfolio-prod`
- `charts/portfolio-next` (values for the published `portfolio-next` chart)
- `charts/pve-exporter`
- `charts/qbittorrent-exporter`

Infrastructure resources are reconciled through the `infra` Argo CD application, which points at the `infra/` Kustomize root.

## 🔍 Pull Request Workflow

GitOps pull requests are checked by two GitHub Actions workflows running on the
self-hosted `homelab-ops-runner`.

### 👀 GitOps preview

`GitOps PR Preview` keeps the required `preview` check available on every pull
request. It only performs Argo CD analysis when the PR changes files under:

- `argocd-apps/`
- `charts/`
- `infra/`
- `manifests/`

Changes under `.github/` are ignored by the GitOps analysis. A workflow-only PR
therefore completes the mandatory `preview` check without creating an Argo CD
preview comment.

For GitOps changes, `friday-pa` maintains a sticky PR comment:

- Existing Applications receive a concise, Kubernetes-aware `dyff` comparison
  of the live and proposed rendered resources
- New Applications receive a separate comment containing the rendered resources
- Repository-level Git patches are not included in preview comments
- Renders that exceed the GitHub comment limit are attached as workflow artifacts
- Changed values and repository paths are mapped back to their affected Applications

### 🚀 Reviewer-approved apply

After a PR that adds or changes a file under `argocd-apps/` is merged,
`GitOps PR Apply` automatically:

1. Checks out the merge commit and determines every affected Application.
2. Posts a sticky `friday-pa` deployment comment with the Application names,
   namespaces, merged change reasons, and merge revision.
3. Waits for approval through the GitHub environment named `olympus`.

Approval applies the unmodified Application definitions from the merge commit, so
their declared source revisions, including `targetRevision: main`, remain intact.
The workflow then starts an Argo CD sync and waits for each Application to become
synced and healthy. If an automated or existing Argo CD operation is already in
progress, it waits and retries without terminating that operation. Transient
manifest-generation deadline and transport failures are retried up to three times.
Argo CD allows up to 300 seconds for server and controller calls to the repo server.

Application deletions are reported in the deployment comment and require manual
cleanup.

The deployment comment is updated with the result:

- `Deployed successfully` when every Application passes sync and health checks
- `Deployment failed` when validation, apply, sync, or health checks fail
- `Deployment skipped` when the `olympus` approval is rejected

Closing a PR without merging does not start this workflow. The post-merge
Application deployment remains optional.

## 🏗️ Infrastructure

The `infra/` tree contains cluster-level resources and supporting app manifests, including:

- Argo CD HTTPRoute resources
- cert-manager issuers and wildcard certificates
- Longhorn routes and platform configuration
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
- Longhorn is the default dynamic storage backend for application PVCs.
- The NFS CSI driver remains installed for static NFS-backed PVs used by Immich and the media library; the dynamic `nfs-csi` StorageClass is not created.
- Grafana dashboards are versioned in Git and deployed through the `grafana-dashboards` chart.
- Renovate tracks dependency updates for charts and container images.

## 🎯 Purpose

This repo documents and operates a Kubernetes-based homelab in a reproducible way. It also serves as a practical DevOps portfolio project covering GitOps, Helm, Kustomize, ingress, storage, secrets, observability, automation, and self-hosted application delivery.
