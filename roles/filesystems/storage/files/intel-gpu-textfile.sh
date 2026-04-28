#!/usr/bin/env bash
#
# intel-gpu-textfile — sample Intel iGPU stats via intel_gpu_top and emit
# Prometheus metrics to the node_exporter textfile collector.
#
# Triggered by intel-gpu-textfile.timer (every 30s).

set -uo pipefail

OUT_DIR=/var/lib/node_exporter/textfile_collector
OUT_FILE="$OUT_DIR/intel_gpu.prom"
TMP=$(mktemp "$OUT_DIR/.intel_gpu.prom.XXXXXX")
trap 'rm -f "$TMP"' EXIT

# intel_gpu_top -J streams JSON samples. Run for ~2s of samples (one warm-up,
# one real reading) and parse the last complete object.
SAMPLE_JSON=$(timeout 2 intel_gpu_top -J -s 1000 2>/dev/null) || true

[[ -n "$SAMPLE_JSON" ]] || { echo "no samples from intel_gpu_top" >&2; exit 0; }

python3 - <<EOF >> "$TMP"
import json, re, sys

text = """$SAMPLE_JSON"""

# intel_gpu_top -J emits a JSON array; each sample is a top-level object.
# Pull the last complete {} block.
last_brace = text.rfind("}")
if last_brace == -1:
    sys.exit(0)
depth, i = 1, last_brace - 1
while i >= 0 and depth > 0:
    c = text[i]
    if c == "}":
        depth += 1
    elif c == "{":
        depth -= 1
    i -= 1
obj_str = text[i+1:last_brace+1]
try:
    o = json.loads(obj_str)
except Exception:
    sys.exit(0)

print("# HELP intel_gpu_freq_mhz GPU frequency.")
print("# TYPE intel_gpu_freq_mhz gauge")
print("# HELP intel_gpu_engine_busy_percent Per-engine busy percent (0-100).")
print("# TYPE intel_gpu_engine_busy_percent gauge")
print("# HELP intel_gpu_power_watts GPU package power draw.")
print("# TYPE intel_gpu_power_watts gauge")
print("# HELP intel_gpu_imc_bytes_per_sec Memory controller bandwidth.")
print("# TYPE intel_gpu_imc_bytes_per_sec gauge")

freq = o.get("frequency", {}) or {}
if "actual" in freq:
    print(f'intel_gpu_freq_mhz{{kind="actual"}} {freq["actual"]}')
if "requested" in freq:
    print(f'intel_gpu_freq_mhz{{kind="requested"}} {freq["requested"]}')

power = o.get("power", {}) or {}
if "GPU" in power:
    print(f'intel_gpu_power_watts{{rail="gpu"}} {power["GPU"]}')
if "Package" in power:
    print(f'intel_gpu_power_watts{{rail="package"}} {power["Package"]}')

imc = o.get("imc-bandwidth", {}) or {}
if "reads" in imc:
    # MiB/s → bytes/s
    print(f'intel_gpu_imc_bytes_per_sec{{direction="read"}} {int(float(imc["reads"]) * 1024 * 1024)}')
if "writes" in imc:
    print(f'intel_gpu_imc_bytes_per_sec{{direction="write"}} {int(float(imc["writes"]) * 1024 * 1024)}')

# Engines: dict like {"Render/3D": {"busy": 12.3, ...}, "Video": {...}}
engines = o.get("engines", {}) or {}
for name, stats in engines.items():
    if not isinstance(stats, dict):
        continue
    busy = stats.get("busy")
    if busy is None:
        continue
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    print(f'intel_gpu_engine_busy_percent{{engine="{safe}"}} {busy}')
EOF

chmod 0644 "$TMP"
mv "$TMP" "$OUT_FILE"
trap - EXIT
