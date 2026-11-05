# General ARC runner for the homelab repositories (helm-charts, homelab-ops, papervault,
# portfolio, portfolio-next, portfolio-tracker, tech-bytes-playground): the apt tools their
# workflows install at run time, and every Node major they ask setup-node for, preinstalled.
# Built by .github/workflows/runner-image.yaml and referenced by digest from
# charts/actions-runner-controller/gha-runner-scale-set/*-values.yaml.
# RailWatch has its own image (images/railwatch-runner) because it also needs Playwright.
ARG RUNNER_VERSION=latest
FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

# setup-node uses a tool-cache entry that satisfies the requested major instead of downloading.
ARG NODE_VERSIONS="22.23.4 24.22.2 26.13.0"

USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git gh gnupg jq unzip zstd python3 python3-venv build-essential \
 && rm -rf /var/lib/apt/lists/*

# The tool cache lives outside /home/runner/_work, which ARC mounts as an empty volume.
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache
RUN set -eux; \
    for v in $NODE_VERSIONS; do \
      dir="$RUNNER_TOOL_CACHE/node/$v/x64"; \
      mkdir -p "$dir"; \
      curl -fsSL "https://nodejs.org/dist/v$v/node-v$v-linux-x64.tar.xz" | tar -xJ -C "$dir" --strip-components=1; \
      touch "$dir.complete"; \
    done; \
    chown -R runner:runner "$RUNNER_TOOL_CACHE"

USER runner
