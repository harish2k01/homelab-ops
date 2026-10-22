# portfolio-tracker local Helm chart

This chart is maintained inside `homelab-ops` for the personal
[portfolio-tracker](https://github.com/harish2k01/portfolio-tracker) application. It is not distributed
through the public Helm repository or the Helm OCI namespace.

Argo CD reads `charts/portfolio-tracker` directly from this repository. `values.yaml`
contains both chart defaults and this homelab's existing configuration. The chart
move preserves the release name, workload names, image identity, routes, Secrets,
and storage configuration.

Render or validate from the repository root:

```sh
helm lint charts/portfolio-tracker
helm template portfolio-tracker charts/portfolio-tracker --namespace portfolio-tracker
```

Routing uses `httpRoutes.internal` and optional `httpRoutes.external`. Each entry
has independent `parentRefs`, `hostnames`, annotations, and rules. The Gateway and
Cloudflare origin are documented in [the gateway reference](../../manifests/traefik/README.md).

The application container remains in `ghcr.io/harish2k01/portfolio-tracker`. Its app release
workflow is independent of this local chart. The reusable
`.github/workflows/update-version.yaml` workflow updates `image.tag`, optional
`image.digest`, and the local chart's `appVersion`, committing both values and
metadata. It does not package or publish this chart.

Application and database Secret references remain in the existing app configuration.
This move does not create or replace live Secrets, databases, PVCs, or workloads.
