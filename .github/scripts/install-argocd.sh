#!/usr/bin/env bash
set -euo pipefail
: "${ARGOCD_VERSION:?Specify a pinned Argo CD CLI version}"
install_dir="${RUNNER_TEMP:?}/gitops-bin"
mkdir -p "$install_dir"
curl --fail --silent --show-error --location --retry 3 --max-time 180 \
  "https://github.com/argoproj/argo-cd/releases/download/${ARGOCD_VERSION}/argocd-linux-amd64" \
  -o "$install_dir/argocd"
curl --fail --silent --show-error --location --retry 3 --max-time 180 \
  "https://github.com/argoproj/argo-cd/releases/download/${ARGOCD_VERSION}/cli_checksums.txt" \
  -o "$install_dir/checksums.txt"
(
  cd "$install_dir"
  grep -E '  argocd-linux-amd64$' checksums.txt | sed 's/argocd-linux-amd64$/argocd/' | sha256sum --check --strict
)
chmod +x "$install_dir/argocd"
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/argocd" version --client
