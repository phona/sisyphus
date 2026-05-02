#!/usr/bin/env bash
# Build redroid-fixed image. Steps explained in README.md.
#
# Inputs (env):
#   UPSTREAM_IMAGE  - upstream redroid tag (default: redroid/redroid:13.0.0_64only-latest)
#   OUT_IMAGE       - output image name (e.g. ghcr.io/phona/redroid-fixed:13.0.0)
#   WORKDIR         - scratch dir (default: /tmp/redroid-fixed-build)
#
# Notes:
#   - cannot use buildkit COPY: triggers the same containerd-2.x mount safety
#     check we're trying to fix. Must go through `docker create | docker export`.
#   - must run on a host with `docker` CLI; CI uses docker/setup-buildx-action.

set -euo pipefail

UPSTREAM_IMAGE="${UPSTREAM_IMAGE:-redroid/redroid:13.0.0_64only-latest}"
OUT_IMAGE="${OUT_IMAGE:?OUT_IMAGE is required, e.g. ghcr.io/phona/redroid-fixed:13.0.0}"
WORKDIR="${WORKDIR:-/tmp/redroid-fixed-build}"

echo "[redroid-fixed] upstream=$UPSTREAM_IMAGE out=$OUT_IMAGE workdir=$WORKDIR" >&2

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR/rfs"

echo "[redroid-fixed] pull upstream..." >&2
docker pull "$UPSTREAM_IMAGE" >&2

echo "[redroid-fixed] extract rootfs via docker create + docker export..." >&2
CID=$(docker create "$UPSTREAM_IMAGE")
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
docker export "$CID" | tar -xf - -C "$WORKDIR/rfs/"

echo "[redroid-fixed] patch /etc/{passwd,group,shadow}..." >&2
printf 'root:x:0:0:root:/:/system/bin/sh\nshell:x:2000:2000:shell:/:/system/bin/sh\n' \
    > "$WORKDIR/rfs/etc/passwd"
printf 'root:x:0:\nshell:x:2000:\n' \
    > "$WORKDIR/rfs/etc/group"
printf 'root:!:0:0:99999:7:::\n' \
    > "$WORKDIR/rfs/etc/shadow"
chmod 0644 "$WORKDIR/rfs/etc/passwd" "$WORKDIR/rfs/etc/group"
chmod 0600 "$WORKDIR/rfs/etc/shadow"

echo "[redroid-fixed] re-tar rootfs..." >&2
tar -C "$WORKDIR/rfs" -cf "$WORKDIR/rfs.tar" .

echo "[redroid-fixed] docker import → $OUT_IMAGE..." >&2
docker import \
    --change 'ENTRYPOINT ["/init"]' \
    --change 'CMD ["qemu=1", "androidboot.hardware=redroid"]' \
    "$WORKDIR/rfs.tar" \
    "$OUT_IMAGE" >&2

echo "[redroid-fixed] built $OUT_IMAGE (run \`docker push\` separately)" >&2
