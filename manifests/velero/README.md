# Velero backups

Velero backs up PVC data from Longhorn volumes into a Kopia repository in a
Garage S3 bucket outside the cluster. Git and Argo CD remain the source of truth
for every other resource, so most restores only bring back PVCs and their data.

| Piece | Where |
|---|---|
| CSI snapshot CRDs, controller, `longhorn-snapshot` VolumeSnapshotClass | `argocd-apps/snapshot-controller.yaml`, `charts/snapshot-controller/values.yaml` |
| Velero server, node-agent, schedules, volume policy, alerts | `argocd-apps/velero.yaml`, `charts/velero/values.yaml` |
| Sealed credentials | this folder |

## How a backup works

1. The `velero-daily` (02:00, kept 14 days) or `velero-weekly` (Sunday 04:00,
   kept 8 weeks) schedule creates a `Backup`.
2. Velero writes every selected Kubernetes object to `backups/<name>/` in the bucket.
3. For each Longhorn PVC it creates a `VolumeSnapshot` (Longhorn `type: snap`,
   instant and local), then a `DataUpload`.
4. node-agent mounts a temporary PVC made from the snapshot and Kopia uploads
   only changed chunks to `kopia/<namespace>/`. The snapshot and temporary PVC
   are deleted afterwards.
5. `velero-volume-policy` skips NFS volumes (protected by ZFS snapshots on
   TrueNAS) and volumes of 10Gi or more (Prometheus, Loki).

## First-time setup

The Applications are not registered by CI. Steps, in order:

1. Register the snapshot controller and check that it is healthy:

   ```bash
   kubectl apply -f argocd-apps/snapshot-controller.yaml
   kubectl get volumesnapshotclass longhorn-snapshot
   ```

2. Create the Garage bucket and key (on the Garage host):

   ```bash
   garage bucket create velero
   garage key create velero
   garage bucket allow --read --write --owner velero --key velero
   ```

3. Set `configuration.backupStorageLocation[0].config.s3Url` in
   `charts/velero/values.yaml` to the Garage S3 endpoint.

4. Seal the S3 credentials (key `cloud`, AWS credentials-file format):

   ```bash
   printf '[default]\naws_access_key_id=%s\naws_secret_access_key=%s\n' "$KEY_ID" "$SECRET" > cloud
   kubectl create secret generic velero-s3-credentials -n velero --from-file=cloud \
     --dry-run=client -o yaml | kubeseal -o yaml > manifests/velero/velero-s3-credentials.yaml
   rm cloud
   ```

5. Seal your own Kopia repository password. Without it Velero creates the
   repository with a built-in default password. Store the password in your
   password manager **and** offline, since it is needed to read backups with
   the Kopia CLI:

   ```bash
   kubectl create secret generic velero-repo-credentials -n velero \
     --from-literal=repository-password="$KOPIA_PASSWORD" \
     --dry-run=client -o yaml | kubeseal -o yaml > manifests/velero/velero-repo-credentials.yaml
   ```

6. Add both files to `manifests/velero/kustomization.yaml`, merge, then register Velero:

   ```bash
   kubectl apply -f argocd-apps/velero.yaml
   velero backup-location get        # PHASE should be Available
   ```

7. Run a first backup and check every volume:

   ```bash
   velero backup create --from-schedule velero-daily --wait
   velero backup describe <name> --details
   ```

## Restore one PVC (Argo CD-managed app)

```bash
argocd app set vaultwarden --sync-policy none
kubectl -n vaultwarden scale deploy vaultwarden --replicas=0
kubectl -n vaultwarden delete pvc vaultwarden
velero restore create vw-fix --from-backup <backup> \
  --include-namespaces vaultwarden --include-resources persistentvolumeclaims --wait
velero restore describe vw-fix --details
argocd app set vaultwarden --sync-policy automated
```

## Test a restore without touching production

```bash
velero restore create immich-test --from-backup <backup> \
  --include-namespaces immich --namespace-mappings immich:immich-test
kubectl delete ns immich-test   # after checking the data
```

## Read backups without Velero or Longhorn

```bash
kopia repository connect s3 --endpoint=<garage-host>:3900 --disable-tls \
  --bucket=velero --prefix=kopia/vaultwarden/ \
  --access-key=<key-id> --secret-access-key=<secret>
kopia snapshot list --all
kopia mount <snapshot-id> ./restore
```
