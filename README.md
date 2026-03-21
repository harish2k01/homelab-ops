# Homelab-Ops

This is a GitOps repository for managing my self-hosted Kubernetes cluster in my homelab. Built with ArgoCD, Helm, and Kustomize, it demonstrates modern Infrastructure as Code (IaC) practices, declarative deployments, and DevOps automation for home infrastructure.

## 🚀 Features

- **GitOps Workflow** - Infrastructure and applications defined as code in Git, automatically synced to Kubernetes via ArgoCD
- **Helm Charts** - Templated deployments for applications and infrastructure (ArgoCD, cert-manager, ingress-nginx, portfolio)
- **Kustomize** - Infrastructure components with environment-specific customization
- **Multi-Environment Support** - Separate dev and prod deployments of the portfolio application
- **Observability Stack** - Prometheus, Grafana, and Alertmanager for comprehensive monitoring
- **Security** - Sealed Secrets for managing sensitive data, cert-manager for SSL/TLS automation
- **Storage** - Longhorn for persistent volume management
- **Ingress Management** - ingress-nginx with custom routing and HTTP routes

## 🏗️ Architecture

```
Kubernetes Cluster
├── ArgoCD (Deployment Controller)
├── Cert-Manager (SSL/TLS)
├── Ingress-Nginx (Ingress Controller)
├── Portfolio (Dev & Prod)
├── Observability Stack (Prometheus, Grafana, Alertmanager)
├── Longhorn (Storage)
└── Sealed Secrets (Secret Management)
```

## 📁 Project Structure

```
argocd-apps/         - ArgoCD Application definitions (deployment targets)
├── argocd.yaml
├── cert-manager.yaml
├── infra.yaml
├── ingress-nginx.yaml
├── portfolio-dev.yaml
├── portfolio-prod.yaml
└── sealed-secrets.yaml

charts/              - Helm charts for applications and infrastructure
├── argocd/          - ArgoCD Helm chart configuration
├── cert-manager/    - Cert-manager Helm chart configuration
├── ingress-nginx/   - Ingress-nginx Helm chart configuration
├── portfolio-dev/   - Portfolio application (dev environment)
├── portfolio-prod/  - Portfolio application (prod environment)
└── sealed-secrets/  - Sealed Secrets Helm chart configuration

infra/               - Kustomize-based infrastructure components
├── kustomization.yaml
├── alertmanager/    - Alert management infrastructure
├── argocd/          - ArgoCD infrastructure setup
├── cert-manager/    - Certificate management & Let's Encrypt integration
├── grafana/         - Grafana dashboards and ingress
├── longhorn/        - Persistent storage configuration
└── prometheus/      - Prometheus monitoring and ingress
```

## 🛠️ Workflow

### GitOps Process
1. **Define** - Infrastructure and applications are defined as code in YAML (Helm charts and Kustomize)
2. **Commit** - Changes are committed to this Git repository
3. **Sync** - ArgoCD automatically detects changes and syncs them to the Kubernetes cluster
4. **Monitor** - Prometheus and Grafana provide visibility into cluster health and performance

### Adding New Applications
1. Create a Helm chart in `charts/`
2. Define values in `charts/<app>/values.yaml`
3. Create an ArgoCD Application in `argocd-apps/<app>.yaml`
4. Commit and push - ArgoCD automatically deploys

### Modifying Infrastructure
1. Edit Kustomize configurations in `infra/`
2. Update `infra/kustomization.yaml` if adding new components
3. Commit and push - ArgoCD applies the changes

## 🚀 Getting Started

### Prerequisites
- Self-hosted Kubernetes cluster (v1.20+) - running on homelab hardware (bare metal, VMs, etc.)
- `kubectl` configured to access your cluster
- `helm` (v3+)
- `kustomize` (optional, for local testing)
- DNS configured to route to your homelab cluster

### Initial Setup
```bash
# Clone the repository
git clone <repository-url>
cd homelab-ops

# Apply infrastructure components to bootstrap the cluster
kubectl apply -k infra/

# Create ArgoCD applications to begin GitOps sync
kubectl apply -f argocd-apps/
```

### Verify Deployment
```bash
# Check ArgoCD applications
kubectl get applications -n argocd

# Monitor sync status
kubectl get applications -n argocd -o wide

# Access ArgoCD dashboard
kubectl port-forward -n argocd svc/argocd-server 8080:443
# Visit https://localhost:8080
```

### Access Services
All services are accessible via ingress endpoints configured in your homelab DNS:

- **ArgoCD** - GitOps deployment management
- **Grafana** - Monitoring dashboards
- **Prometheus** - Metrics and alerting
- **Alertmanager** - Alert notifications
- **Longhorn** - Storage dashboard
- **Portfolio** - Personal portfolio application (dev and prod)

Configure DNS and firewall rules to point to your cluster's ingress controller IP.

## 📋 Key Components

| Component | Purpose | Location |
|-----------|---------|----------|
| **ArgoCD** | GitOps deployment controller | `infra/argocd/`, `charts/argocd/` |
| **Cert-Manager** | Automatic SSL/TLS certificate management | `infra/cert-manager/`, `charts/cert-manager/` |
| **Ingress-Nginx** | HTTP/HTTPS ingress controller | `infra/` (ingress routing) |
| **Portfolio (Dev/Prod)** | Personal portfolio application | `charts/portfolio-dev/`, `charts/portfolio-prod/` |
| **Prometheus** | Metrics collection and alerting | `infra/prometheus/` |
| **Grafana** | Metrics visualization and dashboards | `infra/grafana/` |
| **Alertmanager** | Alert routing and notifications | `infra/alertmanager/` |
| **Longhorn** | Persistent volume management | `infra/longhorn/` |
| **Sealed Secrets** | Encrypted secret management | `charts/sealed-secrets/` |

## 🔐 Secret Management

Sensitive data (API keys, credentials) is managed using Sealed Secrets:
1. Secrets are encrypted using a cluster-specific key
2. Encrypted secrets are committed to Git safely
3. ArgoCD decrypts and applies them at deploy time

## 📊 Monitoring & Observability

- **Prometheus** collects metrics from all cluster components
- **Grafana** visualizes metrics with custom dashboards
- **Alertmanager** handles alert routing and notifications
- Access monitoring dashboards via their ingress endpoints

## 🔄 CI/CD Integration

This repository is designed to work with external CI/CD systems:
- Webhooks can trigger ArgoCD syncs on Git push
- Changes are immediately reflected in the cluster
- No separate deployment steps required

## 📝 Development & Customization

### Adding a New Service
1. Create Helm chart: `mkdir -p charts/my-service/templates`
2. Define `Chart.yaml` and `values.yaml`
3. Add ArgoCD application file: `argocd-apps/my-service.yaml`
4. Commit and push

### Customizing Infrastructure
1. Edit configurations in `infra/`
2. Use Kustomize for environment-specific overrides
3. Commit and push

### Testing Locally
```bash
# Validate Helm charts
helm template charts/my-service/

# Validate Kustomize
kustomize build infra/
```

## 📧 Contact

This project is maintained by **Harish T.**

---

**Note:** This repository is for managing my self-hosted homelab Kubernetes cluster and is part of my learning and skill demonstration project. Sensitive credentials are managed using Sealed Secrets and are never committed to Git.
