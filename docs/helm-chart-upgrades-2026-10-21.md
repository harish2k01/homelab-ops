# Helm chart upgrade review — 2026-10-21

Cross-check of all 30 Argo CD Applications using the published harish2k01 Helm repository against helm-charts main 34b096a592b005e9c5699e3c829b5f4849cde36a. Downloaded the exact current/target chart packages from the published index and rendered them with these homelab values. This change updates 24 chart pins and 22 app images. It does not merge, sync or deploy applications.

| Application | Chart version | App image tag |
| --- | --- | --- |
| adguard-home | 0.1.2 → 0.1.3 | v0.107.76 → v0.107.79 |
| bazarr | 0.1.2 → 0.2.0 | 1.5.6 → 1.6.2 |
| bazarr4k | 0.1.2 → 0.2.0 | 1.5.6 → 1.6.2 |
| bentopdf | 0.1.3 → 0.2.0 | 2.8.4 → 2.8.10 |
| cloudflared | 0.1.1 → 0.2.0 | 2026.3.0 → 2026.10.2 |
| flaresolverr | 0.1.2 → 0.1.3 | v3.4.6 → v3.5.2 |
| homepage | 0.1.0 → 0.1.1 | v1.13.1 → v1.13.2 |
| jellyfin | 0.1.2 → 0.2.0 | 10.11.8 → 10.11.11 |
| portfolio-tracker | 0.1.0 → 0.2.0 | 0.1.0 → 0.7.0 |
| profilarr | 0.1.0 → 0.1.1 | unchanged |
| prowlarr | 0.1.2 → 0.2.0 | 2.3.5 → 2.6.5 |
| pve-exporter | 0.1.0 → 0.2.0 | 3.8.2 → 3.10.1 |
| qbittorrent | 0.1.2 → 0.2.0 | 5.2.0 → 5.2.4 |
| radarr | 0.1.2 → 0.2.0 | 6.1.1 → 6.4.4 |
| radarr4k | 0.1.2 → 0.2.0 | 6.1.1 → 6.4.4 |
| scrutiny | 0.2.2 → 0.2.4 | unchanged |
| seerr | 0.1.2 → 0.2.0 | v3.2.0 → v3.5.0 |
| sonarr | 0.1.2 → 0.2.0 | 4.0.17 → 4.0.20 |
| sonarranime | 0.1.2 → 0.2.0 | 4.0.17 → 4.0.20 |
| speedtest-tracker | 0.1.3 → 0.2.0 | 1.14.0 → 1.15.0 |
| syncthing | 0.1.0 → 0.2.0 | 2.1.0 → 2.1.6 |
| tailscale | 0.1.1 → 0.1.4 | v1.96.5 → v1.104.1 |
| umami | 0.1.0 → 0.2.0 | 3.1.0 → 3.4.0 |
| uptime-kuma | 0.1.2 → 0.2.0 | 2.2.1-slim → 2.5.6-slim |

## Values and compatibility

- Update explicit application image overrides as well as chart pins so old values cannot silently keep the old app running. Preserve existing repositories, routes, namespaces, resource limits, media/device mounts, auth references, persistence and per-installation settings. Add relevant opt-in configuration notes and links to each chart's complete runtime reference and changelog.
- Add migration startup probes to Bazarr/Bazarr4k, Jellyfin, Speedtest Tracker, Syncthing and Umami. Other newly supported Secret/config hooks remain opt-in. New app settings managed by a UI or persisted config are not forced through environment variables.
- Keep every datastore image unchanged: Scrutiny InfluxDB 2.8, Umami PostgreSQL 15-alpine, Portfolio Tracker PostgreSQL 16-alpine, Uptime Kuma MariaDB 11 and the other existing SQL pins. This is not a database-server upgrade. Umami's [target README](https://github.com/umami-software/umami/blob/v3.4.0/README.md) requires PostgreSQL 12.14+, and Portfolio Tracker's [target Compose definition](https://github.com/harish2k01/portfolio-tracker/blob/v0.7.0/docker-compose.yml) explicitly uses 16-alpine. Uptime Kuma still supports MariaDB, but no exact tested server-major matrix or restored-production-data migration was verified here.
- Keep the previous stable BusyBox init tags (Scrutiny 1.36, Tailscale 1.37) rather than inheriting the new chart default 1.38.0, which upstream labels unstable. Scrutiny adopts Recreate rollout semantics to prevent two database writers sharing its PVC; expect brief downtime.
- Add narrowly scoped ignoreDifferences rules plus RespectIgnoreDifferences for only the two changing chart/app-version labels within existing Portfolio Tracker, Umami and Tailscale StatefulSet claim templates. Storage specs and database versions remain managed. Preserve Tailscale's pre-existing TS_KUBE_SECRET ignore rule and per-pod tailnet state.
- Disable the newly introduced PVE QDevice collector until its actual API permissions/use are verified; retain the existing Secret-mounted exporter configuration and ServiceMonitor. Verify an authenticated Proxmox scrape after rollout.
- Preserve Homepage's application-owned /app/config PVC and disabled generated config. No dashboard files are replaced. FlareSolverr browser-wait tuning, Umami TOTP/MCP, Portfolio Tracker logo providers, Tailscale boot timeout and other optional settings are documented without enabling new features or adding credentials.

## Deferred and already current

**Ghost is deferred:** its live and declared database is mysql:9.7, while Ghost's [official supported stack](https://docs.ghost.org/hosting/) lists MySQL 8.0/8.4. Leave chart 0.2.3, Ghost 6.37.0 and MySQL 9.7 unchanged until there is an explicit supported migration/compatibility decision. Do not downgrade the existing database in place. This is a pre-existing unsupported configuration, not a database change introduced here.

Firefly III, Portfolio Next, qBittorrent Exporter, Tor Proxy and Vaultwarden already use the latest published chart versions and are unchanged. Preserve Portfolio Next's newer custom image/digest rather than resetting it to the chart's older default.

## Migration and rollout considerations

Back up the affected app configuration and database together before rollout. Bazarr, Jellyfin, Portfolio Tracker, Syncthing, Umami, Uptime Kuma and other upgraded apps can migrate their own data even though server image versions are unchanged. Preserve encryption/auth keys; a Helm/Argo rollback does not reverse schema migrations. Test restoring a matched backup before relying on it for recovery. No backup of production data or live migration was executed by this review.

Seerr custom consumers must handle discordIDs arrays and the new POST/PUT library sync/update endpoints; removed GET sync/enable parameters cannot be used. qBittorrent's forwarded-host behavior requires explicit reverse-proxy/trusted-proxy configuration in the app. Remove obsolete Speedtest UniFi/Telescope and Syncthing configuration overrides from externally managed settings if any exist; the repository scan found no active occurrences. AdGuard's large-filter download limit and PVE QDevice behavior are documented in the values notes. Review the linked chart changelogs for the complete upstream feature/configuration changes.

Argo CD Applications retain automatic synchronization. Merging this PR can therefore trigger rollouts through the existing GitOps reconciliation; no direct sync was invoked during review. Jellyfin and Portfolio Tracker currently have no live Application/workloads in the inspected cluster, so their validation is repository/package rendering rather than live upgrade verification.

## Validation

All 30 exact published chart packages lint and render successfully with the proposed homelab values. Before/after comparisons verify stable resource identities, Secret contents, PVC specs and mounts, Service/Ingress/HTTPRoute/ServiceMonitor specs, RBAC specs and all datastore/init image pins. Existing semantic values change only at the intended application image tags; new fields are explicitly added and documented. All present live workload images match the declared baseline; missing Jellyfin/Portfolio Tracker resources are noted above. The GitOps PR Preview provides the independent merged-main/live comparison before merge. Chart rendering is not evidence of successful production migrations, external integrations, GPU playback, DNS/tunnel traffic or actual monitoring behavior.
