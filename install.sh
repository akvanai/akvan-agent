#!/bin/sh

set -eu

PROGRAM="Akvan Agent"
AKVAN_HOME=${AKVAN_HOME:-"$HOME/.akvan"}
BIN_DIR=${AKVAN_BIN_DIR:-"$HOME/.local/bin"}
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

Environment overrides:
  AKVAN_HOME        Installation directory (default: ~/.akvan)
  AKVAN_BIN_DIR     Launcher directory (default: ~/.local/bin)
  AKVAN_SKIP_SETUP  Set to 1 to skip interactive setup (intended for CI)
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
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

    printf 'uv was not found; installing it into %s\n' "$BIN_DIR"
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
        printf 'Replacing an incompatible Python environment.\n'
        rm -rf -- "$VENV_DIR"
    fi

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        if python_path=$("$UV" python find '>=3.10' 2>/dev/null); then
            printf 'Using compatible Python at %s.\n' "$python_path"
            "$UV" venv --python "$python_path" "$VENV_DIR"
        else
            printf 'No compatible Python found; installing Python 3.12.\n'
            "$UV" venv --python 3.12 "$VENV_DIR"
        fi
    fi
}

install_akvan() {
    validate_paths
    command -v tar >/dev/null 2>&1 || die "tar is required to install $PROGRAM."
    mkdir -p -- "$AKVAN_HOME" "$BIN_DIR"
    find_or_install_uv

    printf 'Installing %s into %s\n' "$PROGRAM" "$AKVAN_HOME"
    prepare_python
    copy_application
    "$UV" pip install \
        --python "$VENV_DIR/bin/python" \
        --upgrade \
        --reinstall-package akvan-agent \
        "$APP_DIR[telegram]"

    ln -sfn -- "$VENV_DIR/bin/akvan" "$LAUNCHER"

    printf 'Syncing bundled skills...\n'
    AKVAN_HOME="$AKVAN_HOME" "$LAUNCHER" skills sync --quiet || true

    if [ ! -f "$AKVAN_HOME/.env" ] && [ "${AKVAN_SKIP_SETUP:-0}" != "1" ]; then
        printf '\nStarting first-time model configuration...\n'
        AKVAN_HOME="$AKVAN_HOME" "$LAUNCHER" model
    fi

    printf '\n%s installed successfully.\n' "$PROGRAM"
    printf 'Launcher: %s\n' "$LAUNCHER"
    case ":${PATH:-}:" in
        *:"$BIN_DIR":*) ;;
        *) printf 'Open a new terminal after adding %s to your PATH.\n' "$BIN_DIR" ;;
    esac
    printf 'Run it with: akvan\n'
}

remove_launcher() {
    if [ -L "$LAUNCHER" ] && [ "$(readlink "$LAUNCHER")" = "$VENV_DIR/bin/akvan" ]; then
        rm -- "$LAUNCHER"
    fi
}

uninstall_akvan() {
    validate_paths
    remove_launcher
    rm -rf -- "$VENV_DIR" "$APP_DIR"
    printf '%s was uninstalled. User data in %s was preserved.\n' "$PROGRAM" "$AKVAN_HOME"
}

purge_akvan() {
    validate_paths
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
