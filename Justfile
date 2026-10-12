codemirror_version := "5.65.16"
xterm_version := "5.5.0"
xterm_fit_version := "0.10.0"
image := "ghcr.io/essembeh/webgcc"

default:
    @just --list

# Lint, format and typing — like the CI job `check`
check:
    uv run ruff format --check src tests
    uv run ruff check src tests
    uv run mypy src tests

# Unit tests (needs gcc in PATH, clang-format optional) — like the CI job `test`
test pytest_args="":
    uv run -- pytest --cov=webgcc --cov-report=term-missing {{pytest_args}}

# Format and fix what can be auto-fixed
format:
    uv run ruff format src tests
    uv run ruff check --fix src tests

# Known vulnerabilities in the installed dependencies
audit:
    uv run --no-sync -- pip-audit

# Outdated direct dependencies + GitHub actions update (pinned by SHA)
outdated:
    uv tree --outdated --depth 1
    GITHUB_TOKEN=$(gh auth token) uvx gha-update

# Run the web app (http://127.0.0.1:8000 by default, see WEBGCC_* env vars)
run:
    uv run -- webgcc

# Same with auto-reload on source change (development)
dev:
    uv run -- uvicorn webgcc.webapp:app --reload --host 127.0.0.1 --port 8000

# Download the front-end libraries served by the app (works offline afterwards)
vendor:
    #!/usr/bin/env bash
    set -euo pipefail
    out=src/webgcc/static/vendor
    rm -rf "$out" && mkdir -p "$out"
    cm=https://cdnjs.cloudflare.com/ajax/libs/codemirror/{{codemirror_version}}
    for f in codemirror.min.css codemirror.min.js mode/clike/clike.min.js addon/edit/matchbrackets.min.js \
             addon/edit/closebrackets.min.js addon/selection/active-line.min.js; do
        name=$(basename "$f"); [[ $name == codemirror.* ]] || name="codemirror-$name"
        curl -fsSL "$cm/$f" -o "$out/$name"
    done
    curl -fsSL https://raw.githubusercontent.com/codemirror/codemirror5/{{codemirror_version}}/LICENSE -o "$out/LICENSE-codemirror"
    xt=https://cdn.jsdelivr.net/npm/@xterm
    curl -fsSL "$xt/xterm@{{xterm_version}}/css/xterm.min.css" -o "$out/xterm.min.css"
    curl -fsSL "$xt/xterm@{{xterm_version}}/lib/xterm.min.js" -o "$out/xterm.min.js"
    curl -fsSL "$xt/addon-fit@{{xterm_fit_version}}/lib/addon-fit.min.js" -o "$out/xterm-addon-fit.min.js"
    curl -fsSL "$xt/xterm@{{xterm_version}}/LICENSE" -o "$out/LICENSE-xterm"
    ls -la "$out"

# Build the container image (podman)
docker-build tag="latest":
    podman build -t {{image}}:{{tag}} .

# Run the container image, published on localhost only
docker-run tag="latest":
    podman run --rm -it -p 127.0.0.1:8000:8000 --pids-limit 256 --memory 1g {{image}}:{{tag}}

# Run the image published by the CI on ghcr.io (latest release, `edge` for main), pulled first
run-docker tag="latest":
    podman run --rm -it --pull=always -p 127.0.0.1:8000:8000 --pids-limit 256 --memory 1g {{image}}:{{tag}}

# Bump the version, commit and tag: the CI publishes the image on the tag
release bump="patch":
    echo "{{bump}}" | grep -E "^(major|minor|patch)$"
    uv version --bump "{{bump}}"
    git add pyproject.toml uv.lock
    git commit --message "🔖 New release: `uv version --short`"
    git tag "`uv version --short`"

[confirm('Confirm push --tags ?')]
publish:
    git log -1 --pretty="%B" | grep '^🔖 New release: '
    git push
    git push --tags
