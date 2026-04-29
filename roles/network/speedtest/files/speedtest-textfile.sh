#!/usr/bin/env bash
#
# speedtest-textfile — run Ookla speedtest, emit Prometheus metrics to the
# node_exporter textfile collector. Triggered by speedtest-textfile.timer.

set -uo pipefail

OUT_DIR=/var/lib/node_exporter/textfile_collector
OUT_FILE="$OUT_DIR/speedtest.prom"
TMP=$(mktemp "$OUT_DIR/.speedtest.prom.XXXXXX")
trap 'rm -f "$TMP"' EXIT

NOW=$(date +%s)

# Ookla speedtest CLI: --accept-license --accept-gdpr suppresses interactive
# prompts on first run; -f json gives parseable output.
SERVER_ARG=""
if [[ -n "${SPEEDTEST_SERVER_ID:-}" ]]; then
  SERVER_ARG="-s ${SPEEDTEST_SERVER_ID}"
fi

# On first run, the CLI prints the EULA banner above the JSON. Strip
# everything before the opening brace so we always parse just the result.
RAW=$(speedtest --accept-license --accept-gdpr -f json $SERVER_ARG 2>/dev/null) || RAW=""
JSON=$(printf '%s' "$RAW" | sed -n '/^{/,$p' | tail -1)

{
  echo "# HELP speedtest_download_bytes_per_second Download bandwidth."
  echo "# TYPE speedtest_download_bytes_per_second gauge"
  echo "# HELP speedtest_upload_bytes_per_second Upload bandwidth."
  echo "# TYPE speedtest_upload_bytes_per_second gauge"
  echo "# HELP speedtest_ping_seconds Idle latency to the test server."
  echo "# TYPE speedtest_ping_seconds gauge"
  echo "# HELP speedtest_jitter_seconds Idle jitter to the test server."
  echo "# TYPE speedtest_jitter_seconds gauge"
  echo "# HELP speedtest_packet_loss_ratio Packet loss ratio (0-1)."
  echo "# TYPE speedtest_packet_loss_ratio gauge"
  echo "# HELP speedtest_run_success Whether the last run succeeded (1) or failed (0)."
  echo "# TYPE speedtest_run_success gauge"
  echo "# HELP speedtest_last_run_timestamp_seconds Unix timestamp of the last run."
  echo "# TYPE speedtest_last_run_timestamp_seconds gauge"
} >> "$TMP"

if [[ -z "$JSON" ]]; then
  echo "speedtest_run_success 0" >> "$TMP"
  echo "speedtest_last_run_timestamp_seconds $NOW" >> "$TMP"
  chmod 0644 "$TMP" && mv "$TMP" "$OUT_FILE"
  trap - EXIT
  exit 0
fi

python3 - "$JSON" "$NOW" <<'EOF' >> "$TMP"
import json, sys
raw = sys.argv[1]
now = sys.argv[2]
try:
    o = json.loads(raw)
except Exception:
    print(f"speedtest_run_success 0")
    print(f"speedtest_last_run_timestamp_seconds {now}")
    sys.exit(0)

# Ookla CLI v1.x emits bandwidth in bytes/s already.
dl = o.get("download", {}).get("bandwidth")
ul = o.get("upload", {}).get("bandwidth")
ping_ms = o.get("ping", {}).get("latency")
jitter_ms = o.get("ping", {}).get("jitter")
loss_pct = o.get("packetLoss")
server = o.get("server", {}) or {}
sid = server.get("id", "")
sname = server.get("name", "")
isp = o.get("isp", "")

labels = f'isp="{isp}",server_id="{sid}",server_name="{sname}"'

if dl is not None:
    print(f'speedtest_download_bytes_per_second{{{labels}}} {dl}')
if ul is not None:
    print(f'speedtest_upload_bytes_per_second{{{labels}}} {ul}')
if ping_ms is not None:
    print(f'speedtest_ping_seconds{{{labels}}} {ping_ms / 1000.0}')
if jitter_ms is not None:
    print(f'speedtest_jitter_seconds{{{labels}}} {jitter_ms / 1000.0}')
if loss_pct is not None:
    # Ookla reports percent (0-100); convert to ratio.
    print(f'speedtest_packet_loss_ratio{{{labels}}} {float(loss_pct) / 100.0}')

print(f"speedtest_run_success 1")
print(f"speedtest_last_run_timestamp_seconds {now}")
EOF

chmod 0644 "$TMP"
mv "$TMP" "$OUT_FILE"
trap - EXIT
