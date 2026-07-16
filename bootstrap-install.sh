#!/bin/sh

set -eu

REPO="akvanai/akvan-agent"
AKVAN_VERSION=${AKVAN_VERSION:-main}

usage() {
    cat <<EOF
Usage: ./bootstrap-install.sh [--help]

Download Akvan Agent from GitHub and run the local installer.

Environment:
  AKVAN_VERSION   Git ref to install (default: main; use v0.1.0 after release)
  AKVAN_HOME      Passed through to install.sh
  AKVAN_BIN_DIR   Passed through to install.sh
  AKVAN_SKIP_SETUP Passed through to install.sh
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

download_url() {
    case "$AKVAN_VERSION" in
        v*.*.*)
            printf 'https://github.com/%s/archive/refs/tags/%s.tar.gz\n' \
                "$REPO" "$AKVAN_VERSION"
            ;;
        *)
            printf 'https://github.com/%s/archive/refs/heads/%s.tar.gz\n' \
                "$REPO" "$AKVAN_VERSION"
            ;;
    esac
}

fetch_archive() {
    url=$(download_url)
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$url"
    else
        die "curl or wget is required to download Akvan."
    fi
}

main() {
    case ${1:-} in
        --help|-h) usage; exit 0 ;;
        "") ;;
        *) usage >&2; die "Unknown option: $1" ;;
    esac

    command -v tar >/dev/null 2>&1 || die "tar is required to install Akvan."

    tmpdir=$(mktemp -d)
    trap 'rm -rf -- "$tmpdir"' EXIT INT HUP TERM

    printf 'Downloading Akvan %s from GitHub...\n' "$AKVAN_VERSION"
    fetch_archive | tar -xz -C "$tmpdir"

    srcdir=$(find "$tmpdir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    [ -n "$srcdir" ] || die "Could not find extracted source directory."
    [ -x "$srcdir/install.sh" ] || die "Downloaded archive is missing install.sh."

    exec "$srcdir/install.sh"
}

main "$@"
