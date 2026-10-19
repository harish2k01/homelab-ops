#!/usr/bin/env bash
set -euo pipefail
version=1.7.12
# Published in rhysd/actionlint's actionlint_1.7.12_checksums.txt release asset.
checksum=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
archive="${RUNNER_TEMP:?}/actionlint.tar.gz"
install_dir="$RUNNER_TEMP/gitops-bin"
mkdir -p "$install_dir"
curl --fail --silent --show-error --location --retry 3 --max-time 180 \
  "https://github.com/rhysd/actionlint/releases/download/v${version}/actionlint_${version}_linux_amd64.tar.gz" -o "$archive"
echo "$checksum  $archive" | sha256sum --check --strict
tar -xzf "$archive" -C "$install_dir" actionlint
echo "$install_dir" >> "$GITHUB_PATH"
"$install_dir/actionlint" --version
