# ARC runner for RailWatch CI with the tools every job used to install at run time.
# Built by .github/workflows/runner-image.yaml and referenced by digest from
# charts/actions-runner-controller/gha-runner-scale-set/railwatch-values.yaml.
ARG RUNNER_VERSION=latest
FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

ARG NODE_VERSION=24.22.2
# Keep in step with the playwright version in RailWatch's package-lock.json; a newer
# version still works, the job then downloads only its own browser build.
ARG PLAYWRIGHT_VERSION=1.63.0

USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git gh jq unzip zstd python3 build-essential \
 && rm -rf /var/lib/apt/lists/*

# Node.js in the Actions tool cache, so setup-node finds it instead of downloading.
# The cache lives outside /home/runner/_work, which ARC mounts as an empty volume.
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache
RUN set -eux; \
    dir="$RUNNER_TOOL_CACHE/node/$NODE_VERSION/x64"; \
    mkdir -p "$dir"; \
    curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" | tar -xJ -C "$dir" --strip-components=1; \
    touch "$dir.complete"; \
    chown -R runner:runner "$RUNNER_TOOL_CACHE"

# Chromium and its system libraries for the browser suites.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN set -eux; \
    export PATH="$RUNNER_TOOL_CACHE/node/$NODE_VERSION/x64/bin:$PATH"; \
    npx --yes "playwright@$PLAYWRIGHT_VERSION" install --with-deps chromium; \
    rm -rf /root/.npm /var/lib/apt/lists/*; \
    chown -R runner:runner "$PLAYWRIGHT_BROWSERS_PATH"

# Lets workflows skip their own system-package and browser-dependency installs.
ENV RAILWATCH_PREBUILT_RUNNER=1
USER runner
