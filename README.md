# Homelab Ops

GitOps configuration for a self-hosted Kubernetes homelab, using Argo CD, Helm,
and Kustomize to manage infrastructure, platform services, and applications.

The stack includes Traefik and Gateway API, MetalLB, Longhorn and NFS storage,
CloudNativePG, cert-manager, Sealed Secrets, Prometheus, Grafana, Loki, Alloy,
and repository-scoped GitHub Actions runners.

## Repository layout

| Path | Contents |
|---|---|
| [`argocd-apps/`](argocd-apps/) | Argo CD Application definitions |
| [`charts/`](charts/) | Helm values and local charts |
| [`infra/`](infra/) | Shared cluster infrastructure |
| [`manifests/`](manifests/) | Application-specific resources and overlays |
| [`.github/`](.github/) | PR previews, validation, and image-version updates |
| [`gitops-policy.yaml`](gitops-policy.yaml) | Application dependencies and deletion behavior |

PRs show the resources that would be added, removed, or modified in the live
cluster. GitHub Actions does not apply manifests, register Applications, or request
Argo CD syncs. Existing Applications follow their Argo CD sync policies.

## Documentation

The [repository wiki](https://github.com/harish2k01/homelab-ops/wiki) contains the
architecture, application configuration, integrations, deployment model, and
troubleshooting reference.

- [Architecture](https://github.com/harish2k01/homelab-ops/wiki/Architecture)
- [Application catalog](https://github.com/harish2k01/homelab-ops/wiki/Application-catalog)
- [Getting started](https://github.com/harish2k01/homelab-ops/wiki/Getting-started)
- [Networking and access](https://github.com/harish2k01/homelab-ops/wiki/Networking-and-access)
- [Storage and databases](https://github.com/harish2k01/homelab-ops/wiki/Storage-and-databases)
- [Observability](https://github.com/harish2k01/homelab-ops/wiki/Observability)
- [CI and runners](https://github.com/harish2k01/homelab-ops/wiki/CI-and-runners)
- [Troubleshooting](https://github.com/harish2k01/homelab-ops/wiki/Troubleshooting)

This repository describes the homelab's own environment. The wiki explains the
configuration and dependencies involved in adapting it to another cluster.
