# docker/Dockerfile
#
# Build STPyV8 wheels inside a Debian container with a pinned, reproducible
# toolchain and uv-managed CPython versions.
#
# Why bullseye (Debian 11, glibc 2.31): V8 on Linux builds with the prebuilt
# clang that depot_tools downloads (targets ~glibc 2.31), so bullseye is the
# lowest Debian that can compile V8 13. If clang later needs a newer glibc
# (build fails early with "GLIBC_2.3x not found"), bump FROM to
# debian:bookworm (glibc 2.36, gcc 12) -- nothing else changes.
#
# Multiple Python versions are managed by uv (python-build-standalone, which
# ships complete dev headers -> no Debian multiarch pyconfig.h problem).
#
# Usage:
#   docker build -t stpyv8-builder .
#   REPO=/path/to/stpyv8      # the stpyv8 checkout
#   docker run -it -v "$REPO":/stpyv8 stpyv8-builder --wheel
#
# The image ENTRYPOINT is /usr/local/bin/build-stpyv8 (see entrypoint.sh); any
# build.py flags passed to `docker run` are forwarded. Override the Python set
# with -e UV_PYTHONS="3.11 3.12"; interactive shell via --entrypoint bash.
#
# NOTE: V8 / Boost / depot_tools are built under /work (container overlayfs),
# NOT in the bind-mounted repo, so the NTFS-incompatible sysroot file names
# never touch the mounted volume. The repo itself is mounted only for sources
# (plain text) and to collect the produced .so / wheels.

FROM debian:bullseye
ARG DEBIAN_FRONTEND=noninteractive

# One layer: point apt at the Tencent mirror of the frozen Debian archive, then
# install the system toolchain (git + gcc + build libs) and uv (into
# /usr/local/bin, already on PATH). bullseye is EOL, so its suites live under the
# debian-archive mirror (NOT the live mirror, whose suites are inconsistent
# snapshots). The security updates (the +deb11uN versions) ship as MATCHED pairs
# under bullseye-security; enabling BOTH main + security lets apt pull libc6/
# libc6-dev, libssl1.1/libssl-dev, perl/perl-base all at the same revision --
# which resolves the earlier "libc6-dev depends on libc6 = u11 but u14 is to be
# installed" conflicts. We overwrite sources.list wholesale, drop any deb822
# *.sources, and clear stale cached lists. The archive's Release files are
# expired (EOL), hence Check-Valid-Until=false.
RUN set -eu; \
    printf '%s\n' \
        'deb http://mirrors.cloud.tencent.com/debian-archive/debian bullseye main' \
        'deb http://mirrors.cloud.tencent.com/debian-archive/debian-security bullseye-security main' \
        > /etc/apt/sources.list; \
    rm -f /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99no-check-valid-until; \
    rm -rf /var/lib/apt/lists/*; \
    apt update; \
    apt install -y --no-install-recommends \
        curl git build-essential pkg-config sudo ca-certificates xz-utils file \
        libssl-dev zlib1g-dev libffi-dev; \
    curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh; \
    UV_INSTALL_DIR=/usr/local/bin sh /tmp/uv-install.sh; \
    rm -f /tmp/uv-install.sh; \
    uv --version

# Install the uv-managed CPython set under /opt (not /root) so the container can
# also run as an unprivileged host user (build.py --docker passes --user); /root
# is not traversable by other uids, which would make these unreachable. Split
# into two layers so tweaking the pip deps does not re-download interpreters.
ARG UV_PYTHONS="3.9 3.10 3.11 3.12 3.13 3.14"
ENV UV_PYTHON_INSTALL_DIR=/opt/uvpython
RUN uv python install $UV_PYTHONS

# Per interpreter: the uv-managed Pythons carry a PEP 668 EXTERNALLY-MANAGED
# marker (both `pip install` and `uv pip install` refuse to touch them), so drop
# it, then install the build/test/runtime deps. importlib_resources is STPyV8's
# runtime dep on Python < 3.10 (setup.py: `importlib_resources; python_version <
# '3.10'`) and the tests import STPyV8, so it must be present. Expose a default
# interpreter as both `python` (runs build.py) and `python3` (depot_tools'
# vpython3 execs "python3").
RUN set -eu; \
    for v in $UV_PYTHONS; do \
        P="$(uv python find "$v")"; \
        rm -f "$("$P" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')/EXTERNALLY-MANAGED"; \
        "$P" -m ensurepip --upgrade >/dev/null 2>&1 || true; \
        "$P" -m pip install -q --upgrade --root-user-action=ignore pip setuptools wheel pytest importlib_resources; \
    done; \
    chmod -R a+rX /opt/uvpython; \
    ln -sf "$(uv python find 3.10)" /usr/local/bin/python; \
    ln -sf "$(uv python find 3.10)" /usr/local/bin/python3

# The repository and the mounted build tree (/stpyv8, /work) are owned by the
# host user, but this container runs as root; git's "dubious ownership" guard
# (CVE-2022-24765) would then refuse every mounted repo -- V8 and its many
# gclient-managed DEPS sub-repos. Trust all repositories in this throwaway
# build container (system-level so it also covers --entrypoint bash).
RUN git config --system --add safe.directory '*'

# COPY (without BuildKit's --chmod) + an explicit chmod keeps this image
# buildable on the legacy builder too (Docker < 23 defaults to it and rejects
# `COPY --chmod`). The entrypoint must stay executable (it is the ENTRYPOINT).
COPY entrypoint.sh /usr/local/bin/build-stpyv8
RUN chmod 0755 /usr/local/bin/build-stpyv8
ENTRYPOINT ["/usr/local/bin/build-stpyv8"]

WORKDIR /stpyv8
