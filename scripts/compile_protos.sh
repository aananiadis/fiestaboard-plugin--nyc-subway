#!/usr/bin/env bash
# Regenerate the protobuf bindings from the vendored MTA proto sources.
#
# The MTA subway feeds use the GTFS-realtime spec plus NYCT-specific extensions.
# We vendor both proto files UNMODIFIED under proto/com/google/transit/realtime/
# (their canonical import path) and commit the generated *_pb2.py inside the
# plugin package so the plugin needs no compile step at runtime or in CI.
#
# Requires protoc. If protoc is not on PATH, the grpcio-tools pip package
# bundles one: `pip install grpcio-tools` then this script uses it automatically.
#
# Generated and verified with protoc / grpcio-tools (libprotoc 6.x).
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v protoc >/dev/null 2>&1; then
  PROTOC=(protoc)
else
  echo "protoc not found on PATH; falling back to python -m grpc_tools.protoc"
  PROTOC=(python -m grpc_tools.protoc)
fi

PROTO_DIR="proto/com/google/transit/realtime"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"${PROTOC[@]}" -Iproto --python_out="$TMP" \
  "$PROTO_DIR/gtfs-realtime.proto" \
  "$PROTO_DIR/gtfs-realtime-NYCT.proto"

# protoc emits the modules under the proto's canonical package path. Flatten
# them into the plugin package so siblings can `from . import gtfs_realtime_pb2`.
GEN="$TMP/com/google/transit/realtime"
OUT="plugins/nyc_subway"
cp "$GEN/gtfs_realtime_pb2.py" "$OUT/gtfs_realtime_pb2.py"
cp "$GEN/gtfs_realtime_NYCT_pb2.py" "$OUT/gtfs_realtime_NYCT_pb2.py"

# The NYCT module imports the base module via its nested package path. Rewrite
# only that one generated import line to a package-relative import so the flat
# layout resolves when the plugin is loaded as a package. (We rewrite our own
# generated artifact, never the vendored .proto.)
python - <<PY
import re
path = "$OUT/gtfs_realtime_NYCT_pb2.py"
src = open(path).read()
src = re.sub(
    r"from com\.google\.transit\.realtime import gtfs_realtime_pb2 as",
    "from . import gtfs_realtime_pb2 as",
    src,
)
open(path, "w").write(src)
PY

echo "Generated: $OUT/gtfs_realtime_pb2.py $OUT/gtfs_realtime_NYCT_pb2.py"
