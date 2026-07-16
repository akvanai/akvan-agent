#!/bin/sh

set -eu

PROGRAM="Akvan Agent"
AKVAN_HOME=${AKVAN_HOME:-"$HOME/.akvan"}
if [ -n "${AKVAN_BIN_DIR:-}" ]; then
    BIN_DIR="$AKVAN_BIN_DIR"
elif [ "$(id -u 2>/dev/null || printf 1)" = "0" ] && [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    BIN_DIR="/usr/local/bin"
else
    BIN_DIR="$HOME/.local/bin"
fi
VENV_DIR="$AKVAN_HOME/venv"
APP_DIR="$AKVAN_HOME/app"
LAUNCHER="$BIN_DIR/akvan"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
UV=""

usage() {
    cat <<EOF
Usage: ./install.sh [--install | --uninstall | --purge | --help]

Install Akvan Agent for the current user. The installer bootstraps uv and a
compatible Python when needed, installs Akvan, and starts first-time setup.

  --install      Install or update Akvan (default)
  --uninstall    Remove the installed program; keep ~/.akvan user data
  --purge        Remove the program and all Akvan user data
  --help         Show this help

You can also run `akvan uninstall` or `akvan uninstall --purge` after install.

Environment overrides:
  AKVAN_HOME        Installation directory (default: ~/.akvan)
  AKVAN_BIN_DIR     Launcher directory (default: /usr/local/bin for root, otherwise ~/.local/bin)
  AKVAN_SKIP_SETUP  Set to 1 to skip interactive setup (intended for CI)
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

print_banner() {
    printf '\n'
    printf '  ╭────────────────────────────────────────╮\n'
    printf '  │  Akvan Agent · Installer               │\n'
    printf '  ╰────────────────────────────────────────╯\n'
    printf '\n'
}

log_step() {
    printf '  › %s\n' "$1"
}

log_ok() {
    printf '  ✓ %s\n' "$1"
}

log_note() {
    printf '    %s\n' "$1"
}

validate_paths() {
    [ -n "$AKVAN_HOME" ] || die "AKVAN_HOME cannot be empty."
    case "$AKVAN_HOME" in
        /|"$HOME") die "Refusing unsafe AKVAN_HOME: $AKVAN_HOME" ;;
    esac
    [ -n "$BIN_DIR" ] || die "AKVAN_BIN_DIR cannot be empty."
}

find_or_install_uv() {
    if command -v uv >/dev/null 2>&1; then
        UV=$(command -v uv)
        return
    fi
    if [ -x "$BIN_DIR/uv" ]; then
        UV="$BIN_DIR/uv"
        return
    fi

    log_step "Installing uv into $BIN_DIR"
    mkdir -p -- "$BIN_DIR"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR="$BIN_DIR" UV_NO_MODIFY_PATH=1 sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh \
            | env UV_INSTALL_DIR="$BIN_DIR" UV_NO_MODIFY_PATH=1 sh
    else
        die "curl or wget is required to install uv."
    fi

    [ -x "$BIN_DIR/uv" ] || die "uv installation did not create $BIN_DIR/uv."
    UV="$BIN_DIR/uv"
}

copy_application() {
    staging="$AKVAN_HOME/.app.new.$$"
    backup="$AKVAN_HOME/.app.old.$$"
    rm -rf -- "$staging" "$backup"
    mkdir -p -- "$staging"

    tar \
        --exclude='./.git' \
        --exclude='./.venv' \
        --exclude='./.env' \
        --exclude='./.pytest_cache' \
        --exclude='./build' \
        --exclude='./dist' \
        --exclude='./*.egg-info' \
        --exclude='*/__pycache__' \
        -C "$SCRIPT_DIR" -cf - . | tar -C "$staging" -xf -

    if [ -e "$APP_DIR" ]; then
        mv -- "$APP_DIR" "$backup"
    fi
    if ! mv -- "$staging" "$APP_DIR"; then
        [ ! -e "$backup" ] || mv -- "$backup" "$APP_DIR"
        die "Could not activate the new application files."
    fi
    rm -rf -- "$backup"
}

prepare_python() {
    if [ -x "$VENV_DIR/bin/python" ] && ! "$VENV_DIR/bin/python" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
    then
        log_step "Replacing an incompatible Python environment"
        rm -rf -- "$VENV_DIR"
    fi

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        if python_path=$("$UV" python find '>=3.10' 2>/dev/null); then
            log_step "Using compatible Python at $python_path"
            "$UV" venv --python "$python_path" "$VENV_DIR"
        else
            log_step "No compatible Python found; installing Python 3.12"
            "$UV" venv --python 3.12 "$VENV_DIR"
        fi
    fi
}

ensure_bin_dir_on_path() {
    case ":${PATH:-}:" in
        *:"$BIN_DIR":*) return ;;
    esac

    if [ "$BIN_DIR" = "$HOME/.local/bin" ]; then
        path_line="export PATH=\"\$HOME/.local/bin:\$PATH\""
    else
        path_line="export PATH=\"$BIN_DIR:\$PATH\""
    fi

    profile="$HOME/.profile"
    case "${SHELL:-}" in
        */bash) profile="$HOME/.bashrc" ;;
        */zsh) profile="$HOME/.zshrc" ;;
    esac

    touch "$profile" 2>/dev/null || return
    if ! grep -F "$path_line" "$profile" >/dev/null 2>&1; then
        {
            printf "\n# Added by Akvan Agent installer\n"
            printf "%s\n" "$path_line"
        } >>"$profile"
        log_ok "Added $BIN_DIR to PATH in $profile"
    fi
}

install_akvan() {
    validate_paths
    command -v tar >/dev/null 2>&1 || die "tar is required to install $PROGRAM."
    print_banner
    log_step "Preparing installation directories"
    mkdir -p -- "$AKVAN_HOME" "$BIN_DIR"
    find_or_install_uv
    log_ok "Package manager ready"

    log_step "Installing $PROGRAM into $AKVAN_HOME"
    prepare_python
    log_ok "Python environment ready"
    copy_application
    log_step "Installing Python packages"
    "$UV" pip install \
        --python "$VENV_DIR/bin/python" \
        --upgrade \
        --reinstall-package akvan-agent \
        "$APP_DIR[telegram]" >/dev/null
    log_ok "Application installed"

    ln -sfn -- "$VENV_DIR/bin/akvan" "$LAUNCHER"
    log_ok "Launcher linked to $LAUNCHER"
    ensure_bin_dir_on_path

    log_step "Syncing bundled skills"
    AKVAN_HOME="$AKVAN_HOME" "$LAUNCHER" skills sync --quiet || true
    log_ok "Skills synced"

    log_step "Restarting running gateways"
    AKVAN_HOME="$AKVAN_HOME" "$LAUNCHER" gateway restart --quiet || true
    log_ok "Running gateways restarted"

    if [ ! -f "$AKVAN_HOME/.env" ] && [ "${AKVAN_SKIP_SETUP:-0}" != "1" ]; then
        if [ ! -t 0 ] || [ ! -t 1 ]; then
            printf '\n'
            log_note "Skipping first-time model configuration because this installer is not running in an interactive terminal."
            log_note "After install, run: $LAUNCHER model"
            printf '\n'
        else
            printf '\n'
            log_step "Starting first-time model configuration"
            log_note "A setup wizard will open in this terminal."
            printf '\n'
            AKVAN_HOME="$AKVAN_HOME" "$LAUNCHER" model
        fi
    fi

    printf '\n'
    printf '  ╭────────────────────────────────────────╮\n'
    printf '  │  Installation complete                 │\n'
    printf '  ╰────────────────────────────────────────╯\n'
    printf '\n'
    log_note "Launcher: $LAUNCHER"
    case ":${PATH:-}:" in
        *:"$BIN_DIR":*) ;;
        *) log_note "Open a new terminal, or run: PATH=$BIN_DIR:\$PATH" ;;
    esac
    log_note "Run: akvan"
    printf '\n'
}

remove_launcher() {
    if [ -L "$LAUNCHER" ] && [ "$(readlink "$LAUNCHER")" = "$VENV_DIR/bin/akvan" ]; then
        rm -- "$LAUNCHER"
    fi
}

remove_managed_searxng_container() {
    if command -v docker >/dev/null 2>&1; then
        docker rm -f akvan-agent-searxng >/dev/null 2>&1 || true
    fi
}

remove_managed_browser_runtime_container() {
    if command -v docker >/dev/null 2>&1; then
        docker rm -f akvan-agent-browser-runtime >/dev/null 2>&1 || true
    fi
}

remove_managed_containers() {
    remove_managed_searxng_container
    remove_managed_browser_runtime_container
}

uninstall_akvan() {
    validate_paths
    if [ -x "$VENV_DIR/bin/akvan" ]; then
        AKVAN_HOME="$AKVAN_HOME" "$VENV_DIR/bin/akvan" uninstall --yes
        return
    fi
    remove_managed_containers
    remove_launcher
    rm -rf -- "$VENV_DIR" "$APP_DIR"
    printf '%s was uninstalled. User data in %s was preserved.\n' "$PROGRAM" "$AKVAN_HOME"
}

purge_akvan() {
    validate_paths
    if [ -x "$VENV_DIR/bin/akvan" ]; then
        AKVAN_HOME="$AKVAN_HOME" "$VENV_DIR/bin/akvan" uninstall --purge --yes
        return
    fi
    remove_managed_containers
    remove_launcher
    rm -rf -- "$AKVAN_HOME"
    printf '%s and all data in %s were removed.\n' "$PROGRAM" "$AKVAN_HOME"
}

action=${1:---install}
case "$action" in
    --install) install_akvan ;;
    --uninstall) uninstall_akvan ;;
    --purge) purge_akvan ;;
    --help|-h) usage ;;
    *) usage >&2; die "Unknown option: $action" ;;
esac
