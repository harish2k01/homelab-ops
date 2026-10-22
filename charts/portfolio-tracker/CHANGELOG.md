# Upgrade notes

## 0.2.1

### Application changes

None. Application, database and init-image versions are unchanged.

### Chart changes

Use stable identity labels in StatefulSet claim templates instead of chart/app-version labels. Add the documented persistence.claimTemplateLabels maps (under each affected component's persistence settings) for deterministic preservation of existing immutable template labels. Existing installations must pin their current labels before the first upgrade; simply removing historical labels is also an immutable change. No StatefulSet/PVC replacement or database migration is performed. Regression tests compare complete claim templates across simulated chart/app version upgrades and verify legacy-label overrides.

## 0.2.0 — 0.7.0

Upgrade from 0.1.0. Reviewed all releases 0.1.1, 0.2.0, 0.3.0, 0.4.0, 0.4.1, 0.5.0, 0.6.0, 0.6.1, 0.6.2 and 0.7.0, plus the [complete source comparison](https://github.com/harish2k01/portfolio-tracker/compare/v0.1.0...v0.7.0). Includes SIP calculation fixes, UI revamps, provider changes, graph fixes, XIRR and asset-logo support.

Added optional LOGO_PROVIDER configuration and Secret-backed LOGO_DEV_TOKEN/BRANDFETCH_CLIENT_ID fields. No provider is enabled by chart defaults. Upstream selects logo.dev when its token is present, then Brandfetch if its client ID is present; LOGO_PROVIDER selects the preferred configured provider. Logo URLs contain a provider publishable identifier and are returned to browsers, so use provider credentials intended for public logo access.

0.7.0 adds Asset.logoUrl, logoSource and logoUpdatedAt via a Prisma migration. The image entrypoint still runs prisma migrate deploy before starting the app; preserve NEXTAUTH_SECRET and back up PostgreSQL before upgrading. Port3000, SMTP settings and database URL behavior are unchanged. UI refresh is an application-visible change; no data-reset or database-version change is introduced by the chart.

Validation: Helm lint, default and provider/Secret/Ingress/HTTPRoute rendering, generated catalog consistency and catalog tests. Provider API responses and live database migrations were not tested.

### Runtime and database audit

Inspected every explicit runtime env read in target src/lib and the image Dockerfile/entrypoint. All supported application variables are exposed: DATABASE_URL, NEXTAUTH_SECRET, NEXTAUTH_URL, APP_BASE_URL, SMTP_HOST/PORT/FROM/SECURE/USER/PASS, LOGO_PROVIDER, LOGO_DEV_TOKEN and BRANDFETCH_CLIENT_ID. Secrets are supplied through the existing Secret, while public configuration stays in portfolioTracker.env. NODE_ENV is a container/framework runtime control and remains production in the published image.

The target docker-compose.yml explicitly uses PostgreSQL16-alpine, matching the retained bundled database image. Prisma migrations run from the image entrypoint and the new logo cache migration only adds nullable columns. No server-major upgrade is required. Application UI settings/investment data remain database-managed rather than chart defaults. No image-build variables are advertised as runtime configuration.
