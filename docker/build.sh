#!/usr/bin/env bash
# Build the stpyv8-builder image (does NOT run the build inside it).
#
# Usage:
#   ./docker/build.sh
#
# Override the image name with IMAGE=myname ./docker/build.sh.
#
# To actually build stpyv8 (wheels for every supported Python), run the image:
#   docker run -it --rm -v "$PWD":/stpyv8 stpyv8-builder --wheel
# or use the container entrypoint directly with its own flags.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${IMAGE:-stpyv8-builder}"

# Build the image (context = this docker/ dir so entrypoint.sh is available).
docker build -t "$IMAGE" "$SCRIPT_DIR"
