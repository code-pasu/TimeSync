#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="timesync"
DEFAULT_INSTALL_DIR="${HOME}/${APP_NAME}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'EOF'
Usage:
  ./install.sh [repository-url] [install-directory]

Examples:
  ./install.sh https://github.com/USER/REPOSITORY.git "$HOME/TimeSync"
  curl -fsSL https://raw.githubusercontent.com/USER/REPOSITORY/main/install.sh \
    | bash -s -- https://github.com/USER/REPOSITORY.git "$HOME/TimeSync"
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "This installer supports Debian and Ubuntu systems with apt-get." >&2
    exit 1
fi

REPO_URL="${1:-}"
INSTALL_DIR="${2:-$DEFAULT_INSTALL_DIR}"

if [[ -z "$REPO_URL" && -d "$SCRIPT_DIR/.git" ]]; then
    REPO_URL="$(git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
    INSTALL_DIR="$SCRIPT_DIR"
fi

if [[ -z "$REPO_URL" ]]; then
    echo "A GitHub repository URL is required when running this script outside a clone." >&2
    usage >&2
    exit 2
fi

echo "==> Installing Debian/Ubuntu prerequisites"
$SUDO apt-get update
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    git \
    iperf3 \
    iproute2 \
    mininet \
    openvswitch-switch \
    python3 \
    python3-venv

if command -v systemctl >/dev/null 2>&1; then
    echo "==> Starting Open vSwitch"
    $SUDO systemctl enable --now openvswitch-switch || \
        $SUDO systemctl enable --now openvswitch
fi

if [[ "$INSTALL_DIR" != "$SCRIPT_DIR" ]]; then
    if [[ -e "$INSTALL_DIR" && -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
        echo "Install directory is not empty: $INSTALL_DIR" >&2
        exit 1
    fi
    if [[ ! -d "$INSTALL_DIR/.git" ]]; then
        echo "==> Cloning $REPO_URL into $INSTALL_DIR"
        mkdir -p "$(dirname -- "$INSTALL_DIR")"
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv for $USER"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv was installed but is not available on PATH. Open a new shell and rerun the installer." >&2
    exit 1
fi

cd "$INSTALL_DIR"
echo "==> Creating the project environment and installing Python dependencies"
uv sync

echo "==> Verifying the installation"
uv run python -m compileall -q sdn_controller.py time_sync.py topology.py src
mn --version
ovs-vsctl --version | head -n 2

cat <<EOF

Installation complete.
Project: $INSTALL_DIR

Run the controller:
  cd "$INSTALL_DIR"
    uv run osken-manager sdn_controller.py

Run the topology in another terminal:
  cd "$INSTALL_DIR"
    sudo -E "$INSTALL_DIR/.venv/bin/python" topology.py
EOF
