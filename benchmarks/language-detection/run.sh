#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
go_command=${GO:-go}
python_command=${PYTHON:-python3}
build_only=false

case "${1:-}" in
    --help|-h)
        printf 'Usage: bash %s [--build-only | compare.py run options]\n\n' "$0"
        exec "$python_command" "$root/compare.py" run --help
        ;;
    --build-only)
        build_only=true
        shift
        if (( $# != 0 )); then
            printf '%s\n' '--build-only does not accept comparison options' >&2
            exit 2
        fi
        ;;
esac

if [[ $(uname -s) != Linux ]]; then
    printf '%s\n' 'Run this script on Linux, for example inside WSL Ubuntu.' >&2
    exit 1
fi

export CC=${CC:-gcc}
export CXX=${CXX:-g++}
export PKG_CONFIG=${PKG_CONFIG:-pkg-config}
for tool in "$go_command" "$python_command" "$CC" "$CXX" "$PKG_CONFIG" make tar; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Missing build tool: %s\n' "$tool" >&2
        printf '%s\n' 'Requires Linux Go 1.25+, Python 3.10+, gcc, g++, make, tar, and pkg-config.' >&2
        printf '%s\n' 'On Ubuntu/Debian: sudo apt-get install build-essential pkg-config golang-go python3 ca-certificates' >&2
        exit 1
    fi
done
if [[ $("$go_command" env GOHOSTOS) != linux ]]; then
    printf '%s\n' 'GO must point to a native Linux Go installation, not Windows go.exe.' >&2
    exit 1
fi
jobs=${JOBS:-2}
if [[ ! $jobs =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' 'JOBS must be a positive integer.' >&2
    exit 2
fi

protobuf_version=3.17.3
source_directory="$root/.cache/protobuf-$protobuf_version"
prefix="$root/.cache/protobuf-$protobuf_version-install"
"$python_command" "$root/prepare.py" protobuf
if [[ ! -f "$prefix/.complete" ]]; then
    tar -xzf "$root/.cache/protobuf-all-$protobuf_version.tar.gz" -C "$root/.cache"
    (
        cd -- "$source_directory"
        ./configure --prefix="$prefix" --disable-shared --enable-static --with-pic --without-zlib
        make -j"$jobs"
        make install
    )
    touch "$prefix/.complete"
fi

"$python_command" "$root/prepare.py" build --go "$go_command"
if [[ $build_only == false ]]; then
    exec "$python_command" "$root/compare.py" run "$@"
fi