#!/usr/bin/env bash
set -euo pipefail
version=1.12.0
checksum=50b43017a7474c2216694d0b5387439488a1d3c2b5eefe1674cd0e5c18bc0316
archive="${RUNNER_TEMP:?}/dyff.tar.gz"
install_dir="$RUNNER_TEMP/gitops-bin"
mkdir -p "$install_dir"
curl --fail --silent --show-error --location --retry 3 --max-time 180 \
  "https://github.com/homeport/dyff/releases/download/v${version}/dyff_${version}_linux_amd64.tar.gz" -o "$archive"
echo "$checksum  $archive" | sha256sum --check --strict
tar -xzf "$archive" -C "$install_dir" dyff
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/dyff" version
