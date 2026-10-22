#!/usr/bin/env bash
set -euo pipefail
version=1.7.12
# Published in rhysd/actionlint's actionlint_1.7.12_checksums.txt release asset.
checksum=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
source "$(dirname "${BASH_SOURCE[0]}")/gitops-download.sh"
archive="${GITOPS_TOOL_CACHE:-${RUNNER_TEMP:?}/gitops-tool-cache}/actionlint/actionlint_${version}_linux_amd64.tar.gz"
install_dir="$RUNNER_TEMP/gitops-bin"
mkdir -p "$install_dir"
gitops_download_cached \
  "https://github.com/rhysd/actionlint/releases/download/v${version}/actionlint_${version}_linux_amd64.tar.gz" \
  "$checksum" "$archive"
tar -xzf "$archive" -C "$install_dir" actionlint
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/actionlint" --version
