#!/usr/bin/env bash
#
# STPyV8 docker build entrypoint.
#
#   REPO=/path/to/stpyv8
#   docker run -it -v "$REPO":/stpyv8 builder-image --wheel
#
# Any flags after the image name are forwarded to build.py (e.g. --wheel,
# --debug, --skip-tests, --python /path/to/python). V8 / Boost / depot_tools
# are built under /work (container overlayfs, so the NTFS-incompatible sysroot
# file names never hit a bind-mounted volume); the repo is mounted at /stpyv8
# for sources and to collect the produced .so / dist/.
#
# Override the Python set:      -e UV_PYTHONS="3.11 3.12"
# Get an interactive shell:     docker run --entrypoint bash -it builder-image

set -euo pipefail

# Build the extension and Boost with the container gcc; V8 keeps using the
# clang that depot_tools downloads (gn selects it internally, unaffected).
export CC=gcc CXX=g++

# The uv-managed Pythons are installed under /opt at image-build time so the
# container can also run as an unprivileged host user (build.py --docker passes
# --user). Make sure uv discovers them there regardless of the runtime HOME.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/uvpython}"

REPO="${STPYV8_REPO:-/stpyv8}"
cd "$REPO"

# Extension cleaning is controlled by build.py's --clean flag (forwarded here via
# "$@"), so a plain run stays incremental; only --clean purges _STPyV8*.so and
# build/ before building. This matters because a .so left by a host build against
# a newer glibc cannot be imported here ("undefined symbol: __libc_single_threaded").
# Python versions to build for (override via -e UV_PYTHONS="...").
PY_VERSIONS="${UV_PYTHONS:-3.9 3.10 3.11 3.12 3.13 3.14}"

py_args=()
for v in $PY_VERSIONS; do
    py_args+=(--python "$(uv python find "$v")")
done

exec python build.py \
    --depot-home /work/depot_tools \
    --v8-home /work/v8 \
    --boost-root /work/boost \
    "${py_args[@]}" \
    "$@"
