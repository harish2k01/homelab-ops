#!/usr/bin/env bash
set -euo pipefail
: "${ARGOCD_VERSION:?Specify a pinned Argo CD CLI version}"
case "$ARGOCD_VERSION" in
  v3.4.6) checksum=af05f97444a140591a12c136f2be6ffafd95aed03b34a500957ff8aedb998181 ;;
  *) echo "Add a trusted Linux amd64 checksum for Argo CD ${ARGOCD_VERSION} before installing it." >&2; exit 1 ;;
esac
source "$(dirname "${BASH_SOURCE[0]}")/gitops-download.sh"
install_dir="${RUNNER_TEMP:?}/gitops-bin"
cache_dir="${GITOPS_TOOL_CACHE:-$RUNNER_TEMP/gitops-tool-cache}/argocd"
mkdir -p "$install_dir"
gitops_download_cached \
  "https://github.com/argoproj/argo-cd/releases/download/${ARGOCD_VERSION}/argocd-linux-amd64" \
  "$checksum" "$cache_dir/argocd-${ARGOCD_VERSION}-linux-amd64"
install -m 0755 "$cache_dir/argocd-${ARGOCD_VERSION}-linux-amd64" "$install_dir/argocd"
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/argocd" version --client
