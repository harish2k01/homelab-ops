#!/usr/bin/env bash
# Reuse only archives/binaries verified against a checksum from trusted code.
gitops_download_cached() {
  local url="$1" checksum="$2" destination="$3"
  if [ -f "$destination" ] && printf '%s  %s\n' "$checksum" "$destination" | sha256sum --check --status; then
    echo "Using verified tool cache: $(basename "$destination")"
    return
  fi
  mkdir -p "$(dirname "$destination")"
  local download="${destination}.download.$$"
  if ! curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 180 \
    "$url" -o "$download"; then
    rm -f "$download"
    return 1
  fi
  if ! printf '%s  %s\n' "$checksum" "$download" | sha256sum --check --strict; then
    rm -f "$download"
    return 1
  fi
  mv -f "$download" "$destination"
}
