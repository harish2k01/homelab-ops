# Single ARC runner image for every homelab repository (helm-charts, homelab-ops, papervault,
# portfolio, portfolio-next, portfolio-tracker, railwatch, tech-bytes-playground) with the tools their
# workflows used to install at run time, so those steps are gone from the workflows:
#   apt tools (curl git jq unzip zstd python3 build-essential gnupg), GitHub CLI (upstream apt
#   repo, current release), Helm, Node 22/24/26 and Python 3.12/3.13 in the Actions tool cache
#   (setup-node/setup-python find them instead of downloading), semantic-release for Portfolio,
#   Playwright Chromium for RailWatch.
# Built by .github/workflows/runner-image.yaml (monthly and on change) and used as :latest by
# charts/actions-runner-controller/gha-runner-scale-set/*-values.yaml.
ARG RUNNER_VERSION=latest
FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

ARG NODE_VERSIONS="22.23.4 24.22.2 26.13.0"
ARG PYTHON_VERSIONS="3.12.16 3.13.18"
ARG HELM_VERSION=v4.3.1

USER root
ENV DEBIAN_FRONTEND=noninteractive
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates curl git gnupg jq unzip zstd python3 python3-venv build-essential; \
    install -d -m 755 /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl -fsSL "https://get.helm.sh/helm-$HELM_VERSION-linux-amd64.tar.gz" | tar -xz -C /usr/local/bin --strip-components=1 linux-amd64/helm; \
    helm version

# The tool cache lives outside /home/runner/_work, which ARC mounts as an empty volume.
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache
ENV AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
RUN set -eux; \
    for v in $NODE_VERSIONS; do \
      dir="$RUNNER_TOOL_CACHE/node/$v/x64"; \
      mkdir -p "$dir"; \
      curl -fsSL "https://nodejs.org/dist/v$v/node-v$v-linux-x64.tar.xz" | tar -xJ -C "$dir" --strip-components=1; \
      touch "$dir.complete"; \
    done; \
    . /etc/os-release; \
    for v in $PYTHON_VERSIONS; do \
      tmp="$(mktemp -d)"; \
      url="$(curl -fsSL https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json \
        | jq -r --arg v "$v" --arg os "$VERSION_ID" '.[] | select(.version==$v) | .files[] | select(.platform=="linux" and .platform_version==$os and .arch=="x64") | .download_url')"; \
      curl -fsSL "$url" | tar -xz -C "$tmp"; \
      (cd "$tmp" && bash ./setup.sh); \
      rm -rf "$tmp"; \
    done; \
    # Portfolio's release job: semantic-release and plugins on Node 24's global path.
    PATH="$RUNNER_TOOL_CACHE/node/24.22.2/x64/bin:$PATH" npm install -g --no-fund --no-audit \
      semantic-release @semantic-release/github @semantic-release/commit-analyzer \
      @semantic-release/release-notes-generator @semantic-release/exec; \
    rm -rf /root/.npm; \
    chown -R runner:runner "$RUNNER_TOOL_CACHE"

# Chromium and its system libraries for RailWatch's browser suites. Keep in step with the
# playwright version in RailWatch's package-lock.json; a newer one still works, the job then
# downloads only its own browser build.
ARG PLAYWRIGHT_VERSION=1.63.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN set -eux; \
    export PATH="$RUNNER_TOOL_CACHE/node/24.22.2/x64/bin:$PATH"; \
    npx --yes "playwright@$PLAYWRIGHT_VERSION" install --with-deps chromium; \
    rm -rf /root/.npm /var/lib/apt/lists/*; \
    chown -R runner:runner "$PLAYWRIGHT_BROWSERS_PATH"

# RailWatch workflows skip their own system-package and browser-dependency installs on it.
ENV RAILWATCH_PREBUILT_RUNNER=1
USER runner
