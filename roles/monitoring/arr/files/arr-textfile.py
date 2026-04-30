#!/usr/bin/env python3
"""arr-textfile — sample Sonarr/Radarr APIs and emit Prometheus textfile metrics.

Reads ARR_APPS from env (json: [{"name":"sonarr","url":"...","api_key":"..."}, ...]).
Writes to /var/lib/node_exporter/textfile_collector/arr.prom atomically.
"""

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request


OUT_DIR = "/var/lib/node_exporter/textfile_collector"
OUT_FILE = os.path.join(OUT_DIR, "arr.prom")


def fetch(url: str, api_key: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={"X-Api-Key": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect_app(name: str, url: str, api_key: str) -> list[str]:
    """Return Prometheus-formatted lines for one *arr app."""
    out: list[str] = []

    try:
        health = fetch(f"{url}/api/v3/health", api_key)
        out.append(f'arr_health_issues{{app="{name}"}} {len(health)}')
        for issue in health:
            sev = (issue.get("type") or "unknown").lower()
            src = (issue.get("source") or "unknown").replace('"', "'")
            out.append(
                f'arr_health_issue{{app="{name}",severity="{sev}",source="{src}"}} 1'
            )
    except Exception as e:
        print(f"{name}: /health failed: {e}", file=sys.stderr)
        out.append(f'arr_app_up{{app="{name}"}} 0')
        return out

    out.append(f'arr_app_up{{app="{name}"}} 1')

    # Queue: pull every page so totalRecords is honored.
    try:
        page, page_size = 1, 200
        total_records = 0
        failed = warning = pending = 0
        while True:
            q = fetch(
                f"{url}/api/v3/queue?page={page}&pageSize={page_size}"
                "&includeUnknownSeriesItems=true&includeUnknownMovieItems=true",
                api_key,
            )
            records = q.get("records", []) or []
            total_records = q.get("totalRecords", len(records))
            for r in records:
                tds = (r.get("trackedDownloadStatus") or "").lower()
                tdstate = (r.get("trackedDownloadState") or "").lower()
                if tds == "warning":
                    warning += 1
                elif tds == "error":
                    failed += 1
                if tdstate in ("importpending", "importblocked", "failedpending"):
                    pending += 1
            if page * page_size >= total_records or not records:
                break
            page += 1
        out.append(f'arr_queue_total{{app="{name}"}} {total_records}')
        out.append(f'arr_queue_warning{{app="{name}"}} {warning}')
        out.append(f'arr_queue_failed{{app="{name}"}} {failed}')
        out.append(f'arr_queue_pending{{app="{name}"}} {pending}')
    except Exception as e:
        print(f"{name}: /queue failed: {e}", file=sys.stderr)

    # Disk space per root folder.
    try:
        roots = fetch(f"{url}/api/v3/rootfolder", api_key)
        for r in roots:
            path = (r.get("path") or "").replace('"', "'")
            free = r.get("freeSpace")
            if free is not None:
                out.append(
                    f'arr_root_free_bytes{{app="{name}",path="{path}"}} {free}'
                )
    except Exception as e:
        print(f"{name}: /rootfolder failed: {e}", file=sys.stderr)

    return out


def main() -> int:
    apps_json = os.environ.get("ARR_APPS")
    if not apps_json:
        print("ARR_APPS env not set", file=sys.stderr)
        return 1
    apps = json.loads(apps_json)

    now = int(time.time())
    lines = [
        "# HELP arr_app_up Whether the *arr API responded (1) or failed (0).",
        "# TYPE arr_app_up gauge",
        "# HELP arr_health_issues Number of active health issues from /api/v3/health.",
        "# TYPE arr_health_issues gauge",
        "# HELP arr_health_issue Per-issue gauge (always 1) labelled with severity+source.",
        "# TYPE arr_health_issue gauge",
        "# HELP arr_queue_total Total items in download queue.",
        "# TYPE arr_queue_total gauge",
        "# HELP arr_queue_warning Queue items in trackedDownloadStatus=warning.",
        "# TYPE arr_queue_warning gauge",
        "# HELP arr_queue_failed Queue items in trackedDownloadStatus=error.",
        "# TYPE arr_queue_failed gauge",
        "# HELP arr_queue_pending Queue items stuck in import-pending state.",
        "# TYPE arr_queue_pending gauge",
        "# HELP arr_root_free_bytes Free space on each root folder.",
        "# TYPE arr_root_free_bytes gauge",
        "# HELP arr_last_run_timestamp_seconds Unix timestamp of the last collector run.",
        "# TYPE arr_last_run_timestamp_seconds gauge",
    ]
    for app in apps:
        lines.extend(collect_app(app["name"], app["url"], app["api_key"]))
    lines.append(f"arr_last_run_timestamp_seconds {now}")

    os.makedirs(OUT_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".arr.prom.", dir=OUT_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
