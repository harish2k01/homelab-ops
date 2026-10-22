# Internal and external gateways

One Traefik Helm release and Deployment serves both gateways. The existing Argo
CD Application owns the chart and both Gateways. Each app owns its HTTPRoutes. Both
Gateways use the existing `traefik` GatewayClass. No second controller,
GatewayClass, Helm values file, or Argo CD Application is required.

| Service | Type / address | Service port â†’ Traefik entrypoint |
|---|---|---|
| `traefik` | LoadBalancer / `10.10.10.252` | `80 â†’ web:80`, `443 â†’ websecure:443` |
| `traefik-external` | ClusterIP / cluster DNS | `80 â†’ extweb:9080`, `443 â†’ extsecure:9443` |

The existing internal pod ports remain unchanged to avoid unnecessary migration.
The external entrypoints are exposed only through the external Service; the
internal Service publishes only the existing internal entrypoints. Both Services
select the same Traefik pods. Gateway listener ports match the pod entrypoints,
not the externally exposed Service ports.

The internal Gateway restricts listener hostnames to `*.k8s.harish2k01.xyz`.
The external Gateway has separate listeners. App values define public routes under
`httpRoutes.external`, with `parentRefs` selecting `traefik-external-gateway`.
App values under `httpRoutes.internal` and the Immich manifest retain routes referencing
`traefik-gateway`. Matching a hostname does not automatically select a Gateway.

The shared Deployment means restarts and controller failures affect both paths.
Routing is separated by entrypoint and Gateway attachment; the ClusterIP is
reachable by permitted cluster workloads. Network-level restrictions require
additional NetworkPolicy or firewall rules.

## Public routes

All app selection happens in Kubernetes:

| Hostname | Backend |
|---|---|
| `bentopdf.harish2k01.xyz` | `bentopdf/bentopdf:80` |
| `firefly.harish2k01.xyz` | `firefly-iii/firefly-iii:80` |
| `jellyfin.harish2k01.xyz` | `media/jellyfin:8096` |
| `harish2k01.xyz` | `portfolio/portfolio-next:80` |
| `v1.harish2k01.xyz` | `portfolio/portfolio-prod:80` |
| `umami.harish2k01.xyz` | `umami/umami:3000` |
| `immich.harish2k01.xyz` | `immich/immich-server:2283` |
| `harish2k01.in` | `ghost/ghost:2368` (with ActivityPub routing) |

Each application's Helm chart owns its internal and public routes in its namespace.
Immich uses two standalone HTTPRoutes in its own manifest source because its
upstream chart is separate from this chart migration. Backend Services remain in
those same namespaces, so cross-namespace backend ReferenceGrants are unnecessary.
There is no central list of app routes owned by Traefik.

For another Helm-managed public app, add `httpRoutes.external` to that app's values
with the external Gateway references and public hostnames. Keep internal hostnames
under `httpRoutes.internal`. Multiple hostnames apply to every compatible Gateway
reference within an entry; use separate entries to separate access paths.

## Cloudflare Tunnel origin

The existing cloudflared deployment uses a remotely managed tunnel. Configure a
wildcard published hostname `*.harish2k01.xyz` and wildcard DNS pointing to the
tunnel. The apex `harish2k01.xyz` needs a separate mapping to the same origin:

- Service URL: `https://traefik-external.traefik.svc.cluster.local:443`.
- Origin Server Name: `harish2k01.xyz`.
- HTTP Host Header: leave unset to preserve the requested application hostname.
- Keep origin TLS certificate verification enabled.

Both Gateways reuse `traefik/wildcard-k8s-tls`. Its Certificate includes the apex,
`*.harish2k01.xyz`, and the internal wildcard. The fixed Origin Server Name
validates this certificate when connecting to the Service DNS name; it does not
replace the HTTP Host header used for backend routing.

Adding another matching public subdomain requires no per-app tunnel backend
mapping. The wildcard may also forward internal-looking names, so external
HTTPRoutes must never include internal hostnames. Unknown names have no matching
application route. A wildcard hostname is not authentication: preserve existing
Cloudflare Access policies, or enforce authentication in the cluster/app.

Ghost's existing public apex `harish2k01.in` is also served through the external
Gateway. Preserve its separate DNS/tunnel mapping to the same origin; the `.xyz`
wildcard does not cover it. The shared certificate already includes this name.

## Initial rollout and verification

No new Argo CD Application needs registration. The existing Traefik Application
includes the new Service, listeners, and Gateway. App Applications own their routes. The Gateway API
CRDs, certificate, app namespaces, and backend Services must already exist.

The 22 updated remote chart pins use the API introduced by
[helm-charts PR #111](https://github.com/harish2k01/helm-charts/pull/111).
Publish and verify those chart versions before deploying these values; they were
not present in the Helm repository index during the initial migration validation.
Argo CD cannot render unpublished versions. Validation used the PR's chart source
at commit `44828a4a29610377f77c30b99e5479908208a35f`.

The old `httpRoute` key is removed from the migrated app values. Generated route
names now end with `-internal` or `-external`; allow app reconciliation to replace
the old unsuffixed routes. The local portfolio charts use the same named API. `portfolio-next` and
`portfolio-tracker` now include their full charts in this repository; their Argo
CD Applications read local paths rather than published Helm packages.
Headlamp's upstream `httpRoute` values are unrelated and remain unchanged.

The `portfolio-tracker` namespace was absent during server-side dry-run validation;
its Application enables `CreateNamespace=true`. Its render was checked locally,
but the route's live admission and backend resolution require that app deployment.

Coordinate the first rollout: stage the shared Deployment update, external
Service, external Gateway, and public routes before removing existing public
routes from the internal path. Use a staged revision or selective resource sync;
a normal full sync does not coordinate the Cloudflare change. After the new
origin works, switch the wildcard/apex tunnel mappings, then complete the internal
Gateway and app-route changes. This avoids removing an old route before the
replacement can receive tunnel traffic.

Verify:

- Traefik rollout completes and both Services have ready endpoints.
- The internal Service forwards only to `web` / `websecure`; the external Service
  forwards only to `extweb` / `extsecure`.
- Both Gateways report `Accepted` / `Programmed`; routes report `Accepted` /
  `ResolvedRefs` for their intended parent.
- Public hosts work through the external Service using a valid certificate and
  the original HTTP Host header; internal hosts have no application route there.
- Internal hosts work through `.252`; public hosts have no application route
  there after migration.
- External requests work through the wildcard/apex tunnel and retain the intended
  authentication behavior.

Traefik's configured Gateway status-address publisher is shared by the controller.
It copies the internal LoadBalancer address into Gateway status, including the
external Gateway. That status is not per-Gateway Service discovery: cloudflared
must use the external Service DNS above, not `Gateway.status.addresses`. Do not
use the external Gateway's status address to publish DNS records.
