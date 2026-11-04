# railwatch local Helm chart

This chart is maintained inside `homelab-ops` for the personal
[RailWatch](https://github.com/harish2k01/railwatch) application. It is not distributed
through the public Helm repository or the Helm OCI namespace.

Argo CD reads `charts/railwatch` directly from this repository. The chart adopts the
workloads that were previously applied by hand. It keeps their names, selectors,
Services, HTTPRoute, NetworkPolicies, and storage claims, so the first sync updates
those objects in place instead of replacing them.

Render or validate from the repository root:

```sh
helm lint charts/railwatch
helm template railwatch charts/railwatch --namespace railwatch
```

## What it runs

| Resource | Purpose |
|---|---|
| `railwatch-frontend` Deployment | Next.js pages and the same-origin `/api/*` gateway. It has no credentials. |
| `railwatch-backend` Deployment | Fastify API and the once-a-minute scheduler, which uses an advisory lock across replicas. |
| `railwatch-postgres` StatefulSet | PostgreSQL 18 on the existing `railwatch-postgres-data` claim. |
| `railwatch-migrate` Job | Argo CD Sync hook that runs `prisma migrate deploy` with the release image after PostgreSQL and before the app pods. |
| `railwatch-postgres-backup` CronJob | Nightly verified `pg_dump` to the existing `railwatch-postgres-backups` claim. |
| NetworkPolicies | Default deny. Gateway → frontend → backend → PostgreSQL, plus outgoing provider ports. Jobs may reach PostgreSQL only. |

Sync waves: PostgreSQL `-2`, migrations `-1`, application `0`.

## Secrets and storage

The chart never creates Secrets or PersistentVolumeClaims. `existingSecret`
(default `railwatch-secrets`) must provide `POSTGRES_USER`, `POSTGRES_DB`,
`POSTGRES_PASSWORD`, `DATABASE_URL`, `APP_ENCRYPTION_KEY`, `CRON_SECRET`, and
`RATE_LIMIT_SALT`. Keep `APP_ENCRYPTION_KEY` backed up separately. It is needed to read
stored plans and ticket files.

## Releases

The RailWatch release workflow calls this repository's reusable
`.github/workflows/update-version.yaml` after publishing an image. That workflow
commits the new `image.tag`, `image.digest`, and `appVersion` here, and Argo CD
then syncs the migration hook and rollout. The RailWatch repository needs the
`GH_APP_ID` and `GH_APP_PRIVATE_KEY` secrets for the same GitHub App the other
application repositories use.
