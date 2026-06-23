#!/usr/bin/env python3
"""immich-textfile — sample the Immich jobs API and emit Prometheus textfile metrics.

Reads config from env:
  IMMICH_URL               base URL (e.g. http://127.0.0.1:2283)
  IMMICH_EMAIL             admin email (login)
  IMMICH_PASSWORD          admin password (login)
  IMMICH_ALERT_PAUSED_QUEUES  comma-separated queue names whose paused state is a fault
  IMMICH_ALERT_EMAIL_TO    recipient for the paused-queue alert (optional)
  IMMICH_ALERT_EMAIL_FROM  sender for the alert (optional)

Writes /var/lib/node_exporter/textfile_collector/immich.prom atomically.

Why this exists: a single corrupt photo once segfaulted libvips inside
immich-server, which BullMQ retried on every restart -> nightly crash-loop.
The stopgap was pausing the whole thumbnail queue from the UI. That state is
invisible to Kuma's HTTP check (the server stays "healthy"), so it silently
blocked thumbnails for every new upload for weeks. This collector exports the
per-queue paused/failed/waiting gauges AND emails when an alertable queue is
left paused, so the next occurrence surfaces within one timer interval.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

OUT_DIR = "/var/lib/node_exporter/textfile_collector"
OUT_FILE = os.path.join(OUT_DIR, "immich.prom")
STATE_DIR = "/var/lib/immich-monitor"
STATE_FILE = os.path.join(STATE_DIR, "alert-state.json")


def _request(url, data=None, headers=None, timeout=15):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login(base, email, password):
    res = _request(f"{base}/api/auth/login", data={"email": email, "password": password})
    return res["accessToken"]


def get_jobs(base, token):
    return _request(f"{base}/api/jobs", headers={"Authorization": f"Bearer {token}"})


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".alert-state.", dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def send_email(to_addr, from_addr, subject, body):
    """Pipe a message to msmtp (configured globally with the postmark account)."""
    msg = f"From: {from_addr}\nTo: {to_addr}\nSubject: {subject}\n\n{body}\n"
    subprocess.run(
        ["msmtp", to_addr],
        input=msg.encode("utf-8"),
        check=True,
        timeout=30,
    )


def write_metrics(lines):
    os.makedirs(OUT_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".immich.prom.", dir=OUT_DIR)
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


def main():
    base = os.environ.get("IMMICH_URL", "http://127.0.0.1:2283").rstrip("/")
    email = os.environ.get("IMMICH_EMAIL")
    password = os.environ.get("IMMICH_PASSWORD")
    alert_queues = [
        q.strip()
        for q in os.environ.get("IMMICH_ALERT_PAUSED_QUEUES", "thumbnailGeneration").split(",")
        if q.strip()
    ]
    alert_to = os.environ.get("IMMICH_ALERT_EMAIL_TO")
    alert_from = os.environ.get("IMMICH_ALERT_EMAIL_FROM")

    now = int(time.time())
    lines = [
        "# HELP immich_monitor_up Whether the Immich jobs API responded (1) or failed (0).",
        "# TYPE immich_monitor_up gauge",
        "# HELP immich_queue_paused Whether a job queue is paused (1) or running (0).",
        "# TYPE immich_queue_paused gauge",
        "# HELP immich_queue_active Whether a job queue is currently processing (1) or idle (0).",
        "# TYPE immich_queue_active gauge",
        "# HELP immich_queue_jobs Jobs in a queue by state (active/waiting/failed/delayed).",
        "# TYPE immich_queue_jobs gauge",
        "# HELP immich_photos_total Total photos stored across all users.",
        "# TYPE immich_photos_total gauge",
        "# HELP immich_videos_total Total videos stored across all users.",
        "# TYPE immich_videos_total gauge",
        "# HELP immich_storage_usage_bytes Total bytes used by all assets.",
        "# TYPE immich_storage_usage_bytes gauge",
        "# HELP immich_storage_usage_kind_bytes Bytes used, split by photos vs videos.",
        "# TYPE immich_storage_usage_kind_bytes gauge",
        "# HELP immich_user_photos Photos stored per user.",
        "# TYPE immich_user_photos gauge",
        "# HELP immich_user_videos Videos stored per user.",
        "# TYPE immich_user_videos gauge",
        "# HELP immich_user_usage_bytes Bytes used per user.",
        "# TYPE immich_user_usage_bytes gauge",
        "# HELP immich_user_quota_bytes Storage quota per user (only if a quota is set).",
        "# TYPE immich_user_quota_bytes gauge",
        "# HELP immich_users_total Number of non-deleted user accounts.",
        "# TYPE immich_users_total gauge",
        "# HELP immich_server_info Immich server version (value always 1).",
        "# TYPE immich_server_info gauge",
        "# HELP immich_monitor_last_run_timestamp_seconds Unix timestamp of the last collector run.",
        "# TYPE immich_monitor_last_run_timestamp_seconds gauge",
    ]

    try:
        token = login(base, email, password)
        jobs = get_jobs(base, token)
    except Exception as e:
        print(f"immich jobs API failed: {e}", file=sys.stderr)
        lines.append("immich_monitor_up 0")
        lines.append(f"immich_monitor_last_run_timestamp_seconds {now}")
        write_metrics(lines)
        # API-down is already covered by the Kuma HTTP monitor; don't double-alert.
        return 0

    lines.append("immich_monitor_up 1")

    state = load_state()
    newly_paused = []
    recovered = []

    for name, q in sorted(jobs.items()):
        status = q.get("queueStatus", {}) or {}
        counts = q.get("jobCounts", {}) or {}
        paused = 1 if status.get("isPaused") else 0
        active = 1 if status.get("isActive") else 0
        lines.append(f'immich_queue_paused{{queue="{name}"}} {paused}')
        lines.append(f'immich_queue_active{{queue="{name}"}} {active}')
        for st in ("active", "waiting", "failed", "delayed", "paused"):
            lines.append(
                f'immich_queue_jobs{{queue="{name}",state="{st}"}} {int(counts.get(st, 0) or 0)}'
            )

        if name in alert_queues:
            was_alerted = bool(state.get(name))
            if paused and not was_alerted:
                newly_paused.append((name, counts))
                state[name] = True
            elif not paused and was_alerted:
                recovered.append(name)
                state[name] = False

    # ── Usage / library stats (best-effort; never blocks job metrics) ──
    auth = {"Authorization": f"Bearer {token}"}
    try:
        stats = _request(f"{base}/api/server/statistics", headers=auth)
        lines.append(f'immich_photos_total {int(stats.get("photos", 0))}')
        lines.append(f'immich_videos_total {int(stats.get("videos", 0))}')
        lines.append(f'immich_storage_usage_bytes {int(stats.get("usage", 0))}')
        lines.append(f'immich_storage_usage_kind_bytes{{kind="photos"}} {int(stats.get("usagePhotos", 0))}')
        lines.append(f'immich_storage_usage_kind_bytes{{kind="videos"}} {int(stats.get("usageVideos", 0))}')
        for u in stats.get("usageByUser", []) or []:
            name = (u.get("userName") or "unknown").replace('"', "'")
            lines.append(f'immich_user_photos{{user="{name}"}} {int(u.get("photos", 0))}')
            lines.append(f'immich_user_videos{{user="{name}"}} {int(u.get("videos", 0))}')
            lines.append(f'immich_user_usage_bytes{{user="{name}"}} {int(u.get("usage", 0))}')
            quota = u.get("quotaSizeInBytes")
            if quota:
                lines.append(f'immich_user_quota_bytes{{user="{name}"}} {int(quota)}')
    except Exception as e:
        print(f"statistics failed: {e}", file=sys.stderr)

    try:
        users = _request(f"{base}/api/admin/users", headers=auth)
        active = [u for u in users if not u.get("deletedAt")]
        lines.append(f"immich_users_total {len(active)}")
    except Exception as e:
        print(f"users failed: {e}", file=sys.stderr)

    try:
        v = _request(f"{base}/api/server/version", headers=auth)
        ver = f'{v.get("major", 0)}.{v.get("minor", 0)}.{v.get("patch", 0)}'
        lines.append(f'immich_server_info{{version="{ver}"}} 1')
    except Exception as e:
        print(f"version failed: {e}", file=sys.stderr)

    lines.append(f"immich_monitor_last_run_timestamp_seconds {now}")
    write_metrics(lines)

    # Email on transition only, so the timer doesn't spam every interval.
    if alert_to and alert_from:
        try:
            for name, counts in newly_paused:
                send_email(
                    alert_to,
                    alert_from,
                    f"[immich] job queue '{name}' is PAUSED",
                    (
                        f"The Immich '{name}' queue is paused, so no new jobs are being "
                        f"processed. New uploads will silently go without thumbnails until "
                        f"it is resumed.\n\n"
                        f"Job counts: {json.dumps(counts)}\n\n"
                        f"This is usually a leftover manual pause (the historical stopgap "
                        f"for a corrupt image that crash-looped libvips). Check for a poison "
                        f"asset, then resume via the jobs API.\n"
                    ),
                )
                print(f"alert sent: queue '{name}' paused", file=sys.stderr)
            for name in recovered:
                send_email(
                    alert_to,
                    alert_from,
                    f"[immich] job queue '{name}' resumed",
                    f"The Immich '{name}' queue is running again.\n",
                )
                print(f"alert sent: queue '{name}' recovered", file=sys.stderr)
        except Exception as e:
            print(f"alert email failed: {e}", file=sys.stderr)
        finally:
            save_state(state)
    else:
        save_state(state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
