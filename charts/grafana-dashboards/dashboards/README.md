Place dashboard JSON files under subfolders in this directory.

Example layout:
- dashboards/Kubernetes/cluster-overview.json
- dashboards/Applications/my-app.json

Each subfolder becomes a Grafana folder via the sidecar folder annotation.
Each dashboard JSON file renders as its own ConfigMap.
Deleting a single dashboard file will remove only that dashboard's ConfigMap.
