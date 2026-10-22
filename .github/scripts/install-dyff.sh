#!/usr/bin/env bash
set -euo pipefail
version=1.12.0
checksum=50b43017a7474c2216694d0b5387439488a1d3c2b5eefe1674cd0e5c18bc0316
source "$(dirname "${BASH_SOURCE[0]}")/gitops-download.sh"
archive="${GITOPS_TOOL_CACHE:-${RUNNER_TEMP:?}/gitops-tool-cache}/dyff/dyff_${version}_linux_amd64.tar.gz"
install_dir="$RUNNER_TEMP/gitops-bin"
mkdir -p "$install_dir"
gitops_download_cached \
  "https://github.com/homeport/dyff/releases/download/v${version}/dyff_${version}_linux_amd64.tar.gz" \
  "$checksum" "$archive"
tar -xzf "$archive" -C "$install_dir" dyff
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/dyff" version
