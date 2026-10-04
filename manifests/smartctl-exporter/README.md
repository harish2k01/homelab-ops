# Physical SSD SMART monitoring

The `smartctl-exporter` Argo CD Application uses chart 0.17.1 (exporter
v0.14.0), a dedicated privileged `disk-monitoring` namespace, and a ServiceMonitor
selected by the existing kube-prometheus-stack release label. The PrometheusRule
has the same release label. No Talos extension or host package installation is
required. There is no externally exposed exporter endpoint.

## Coverage

Read-only Talos disk inventory on 2026-10-04 showed:

| Node | Boot disk | Collection |
| --- | --- | --- |
| hermes | Samsung MZNLN128 SATA, `/dev/sda` | Included |
| athena | Kingston SA400S3 SATA, `/dev/sda` | Included |
| zeus | QEMU virtual disk | Physical SMART must be collected on Proxmox |
| apollo | QEMU virtual disk | Physical SMART must be collected on Proxmox |

Node affinity and explicit device selection avoid Longhorn iSCSI volumes. When
adding nodes or replacing disks, verify `talosctl get disks`, then adjust affinity
and devices in `charts/smartctl-exporter/values.yaml`. Use chart `extraInstances`
with separate affinity/device configuration for nodes with different disk paths.
The Proxmox API exporter does not replace physical SMART collection. Proxmox
host exporter installation remains separate work requiring host access.

## Alerts and dashboard

Alerts cover exporter/pod availability, missing SMART status, SMART failure,
temperature, SATA sector errors, and NVMe warnings, media errors, spare and wear.
60 C is an initial temperature threshold and should be checked against vendor
ratings. SATA raw attributes 5, 197 and 198 use conventional sector meanings;
confirm support and interpretation on each device. NVMe rules remain inactive
when those metrics are absent. SATA wear indicators are vendor-specific and are
not mapped to a universal remaining-life percentage.

Grafana Git Sync consumes `Infra/ssd-health.json` in the separate
`grafana-dashboards` repository. It includes node/device filters, inventory,
temperature, sector errors and NVMe panels. Missing data remains unavailable.
SMART reads are cached for five minutes; Prometheus scrapes every minute.

## Rollout and verification

Merge both repositories through their normal review workflow. Follow the
`olympus` approval gate for the new Argo CD Application. Grafana Git Sync must
receive the dashboard commit; the legacy dashboard chart is not updated here.

After sync:

```sh
kubectl -n disk-monitoring get daemonset,pods,servicemonitor,prometheusrule
kubectl -n disk-monitoring logs -l app.kubernetes.io/name=prometheus-smartctl-exporter
```

Verify two ready exporter pods (hermes and athena), two Prometheus targets with
`node` labels, and `smartctl_device_smart_status` and temperature for both SSDs.
Inspect raw attributes before interpreting missing sector data as zero. Check
Prometheus rule health and Grafana rendering after Git Sync. Unsupported metrics
must display No data. An exporter responding successfully is not proof of disk
health. No SMART self-tests are started by this integration.
