#!/usr/bin/env bash
set -euo pipefail
version=v4.53.6
# SHA-256 digest published for this asset by the official GitHub release.
checksum=c5f056448f973ae7d39b5401949648a78f2dc1947d6a8eb65be60d5c504b9385
install_dir="${RUNNER_TEMP:?}/gitops-bin"
mkdir -p "$install_dir"
curl --fail --silent --show-error --location --retry 3 --retry-max-time 600 \
  --connect-timeout 15 --max-time 180 \
  "https://github.com/mikefarah/yq/releases/download/${version}/yq_linux_amd64" -o "$install_dir/yq"
echo "$checksum  $install_dir/yq" | sha256sum --check --strict
chmod +x "$install_dir/yq"
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/yq" --version
