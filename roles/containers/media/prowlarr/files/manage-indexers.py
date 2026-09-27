#!/usr/bin/env python3
"""manage-indexers — converge Prowlarr indexer state to a vault-defined list.

Reads the desired indexer list as JSON from $PROWLARR_INDEXERS, the API key
from $PROWLARR_API_KEY, and the API base URL from $PROWLARR_URL (default
http://127.0.0.1:9696). For each entry, ensures a matching Prowlarr indexer
exists with the right baseUrl + apiKey and application profile; creates it if
missing, updates if present and drifted. Names in
$PROWLARR_INTERACTIVE_ONLY_INDEXERS are assigned a managed profile that turns
off RSS and automatic search while retaining interactive search. Indexers in
Prowlarr but absent from the desired list are left alone (non-destructive —
UI-added indexers survive).

Prints "changed" on stdout if any create/update happened (used by the
Ansible task's changed_when).
"""

import copy
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE = os.environ.get("PROWLARR_URL", "http://127.0.0.1:9696").rstrip("/")
KEY = os.environ.get("PROWLARR_API_KEY") or sys.exit("PROWLARR_API_KEY not set")
INTERACTIVE_ONLY_INDEXERS = set(
    json.loads(os.environ.get("PROWLARR_INTERACTIVE_ONLY_INDEXERS", "[]"))
)
INTERACTIVE_ONLY_PROFILE = {
    "name": "Interactive only",
    "enableRss": False,
    "enableAutomaticSearch": False,
    "enableInteractiveSearch": True,
    "minimumSeeders": 1,
}


def call(method: str, path: str, body=None):
    data = None
    headers = {"X-Api-Key": KEY, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {path} -> {e.code}: {body}")


def newznab_payload(
    name: str,
    base_url: str,
    api_key: str,
    app_profile_id: int = 1,
) -> dict:
    """Build a POST/PUT body for a Newznab indexer."""
    return {
        "enable": True,
        "redirect": True,
        "supportsRss": True,
        "supportsSearch": True,
        "supportsRedirect": True,
        "supportsPagination": True,
        "appProfileId": app_profile_id,
        "protocol": "usenet",
        "privacy": "private",
        "priority": 25,
        "name": name,
        "implementation": "Newznab",
        "implementationName": "Newznab",
        "configContract": "NewznabSettings",
        "tags": [],
        "fields": [
            {"name": "baseUrl", "value": base_url},
            {"name": "apiPath", "value": "/api"},
            {"name": "apiKey", "value": api_key},
            {"name": "additionalParameters", "value": ""},
            {"name": "vipExpiration", "value": ""},
            {"name": "baseSettings.queryLimit", "value": None},
            {"name": "baseSettings.grabLimit", "value": None},
            {"name": "baseSettings.limitsUnit", "value": 0},
        ],
    }


def field_value(indexer: dict, field_name: str):
    for f in indexer.get("fields", []):
        if f.get("name") == field_name:
            return f.get("value")
    return None


def app_profile_needs_update(existing: dict, desired: dict) -> bool:
    return any(existing.get(key) != value for key, value in desired.items())


def ensure_app_profile(desired: dict) -> tuple[int, bool]:
    profiles = call("GET", "/api/v1/appprofile")
    existing = next(
        (profile for profile in profiles if profile.get("name") == desired["name"]),
        None,
    )
    if existing is None:
        created = call("POST", "/api/v1/appprofile", desired)
        return int(created["id"]), True

    if app_profile_needs_update(existing, desired):
        payload = {**desired, "id": existing["id"]}
        updated = call("PUT", f"/api/v1/appprofile/{existing['id']}", payload)
        return int((updated or payload)["id"]), True

    return int(existing["id"]), False


def needs_update(existing: dict, desired: dict) -> bool:
    """True iff managed settings differ (apiKey is masked on GET)."""
    if not existing.get("enable", True):
        return True
    if field_value(existing, "baseUrl") != field_value(desired, "baseUrl"):
        return True
    if field_value(existing, "apiPath") != field_value(desired, "apiPath"):
        return True
    if existing.get("appProfileId", 1) != desired.get("appProfileId", 1):
        return True
    return False


def main() -> int:
    desired = json.loads(os.environ["PROWLARR_INDEXERS"])
    existing = {i["name"]: i for i in call("GET", "/api/v1/indexer")}

    changed = False
    interactive_only_profile_id = None
    if INTERACTIVE_ONLY_INDEXERS:
        interactive_only_profile_id, profile_changed = ensure_app_profile(
            INTERACTIVE_ONLY_PROFILE
        )
        changed |= profile_changed

    for spec in desired:
        name = spec["name"]
        app_profile_id = (
            interactive_only_profile_id if name in INTERACTIVE_ONLY_INDEXERS else 1
        )
        payload = newznab_payload(
            name,
            spec["base_url"],
            spec["api_key"],
            app_profile_id=app_profile_id,
        )
        if name in existing:
            cur = existing[name]
            if needs_update(cur, payload):
                payload["id"] = cur["id"]
                call("PUT", f"/api/v1/indexer/{cur['id']}?forceSave=true", payload)
                print(f"updated: {name}", file=sys.stderr)
                changed = True
            else:
                print(f"unchanged: {name}", file=sys.stderr)
        else:
            call("POST", "/api/v1/indexer?forceSave=true", payload)
            print(f"created: {name}", file=sys.stderr)
            changed = True

    if changed:
        print("changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
