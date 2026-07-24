#!/usr/bin/env python3
"""Converge Whisparr's local *arr integrations.

Reads Whisparr's generated API key from config.xml, then configures the app
through supported Whisparr and Prowlarr APIs.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"{name} is required")
    return value


def env_bool(name, default):
    value = str(env(name, default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    sys.exit(f"{name} must be a boolean value")


WHISPARR_URL = env("WHISPARR_URL").rstrip("/")
WHISPARR_CONFIG_XML = env("WHISPARR_CONFIG_XML")
WHISPARR_ROOT_FOLDER = env("WHISPARR_ROOT_FOLDER")
SABNZBD_HOST = env("SABNZBD_HOST")
SABNZBD_PORT = int(env("SABNZBD_PORT", "8080"))
SABNZBD_API_KEY = env("SABNZBD_API_KEY")
SABNZBD_CATEGORY = env("SABNZBD_CATEGORY", "whisparr")
PROWLARR_URL = env("PROWLARR_URL").rstrip("/")
PROWLARR_API_KEY = env("PROWLARR_API_KEY")
PROWLARR_SELF_URL = env("PROWLARR_SELF_URL")
PROWLARR_WHISPARR_BASE_URL = env("PROWLARR_WHISPARR_BASE_URL")
PROWLARR_SYNC_CATEGORIES = json.loads(env("PROWLARR_SYNC_CATEGORIES"))
PROWLARR_DOWNLOAD_CLIENT_NAME = env("PROWLARR_DOWNLOAD_CLIENT_NAME", "SABnzbd")
PROWLARR_DOWNLOAD_CLIENT_DEFAULT_CATEGORY = env(
    "PROWLARR_DOWNLOAD_CLIENT_DEFAULT_CATEGORY",
    "movies",
)
PROWLARR_DOWNLOAD_CLIENT_MAPPED_CATEGORIES = json.loads(
    env("PROWLARR_DOWNLOAD_CLIENT_MAPPED_CATEGORIES", "[]")
)
WHISPARR_AUTHENTICATION_METHOD = env("WHISPARR_AUTHENTICATION_METHOD", "none")
WHISPARR_AUTHENTICATION_REQUIRED = env(
    "WHISPARR_AUTHENTICATION_REQUIRED",
    "disabledForLocalAddresses",
)
WHISPARR_LOG_LEVEL = env("WHISPARR_LOG_LEVEL", "info")
WHISPARR_CONSOLE_LOG_LEVEL = env("WHISPARR_CONSOLE_LOG_LEVEL", "info")
WHISPARR_INDEXER_CONFIG = {
    "searchStudioCode": env_bool("WHISPARR_SEARCH_STUDIO_CODE", "true"),
    "searchTitleOnly": env_bool("WHISPARR_SEARCH_TITLE_ONLY", "false"),
    "searchTitleDate": env_bool("WHISPARR_SEARCH_TITLE_DATE", "false"),
    "searchStudioDate": env_bool("WHISPARR_SEARCH_STUDIO_DATE", "true"),
    "searchStudioTitle": env_bool("WHISPARR_SEARCH_STUDIO_TITLE", "true"),
    "searchDateFormat": env("WHISPARR_SEARCH_DATE_FORMAT", "yymmdd"),
    "searchStudioFormat": env("WHISPARR_SEARCH_STUDIO_FORMAT", "clean"),
}


def whisparr_api_key():
    root = ET.parse(WHISPARR_CONFIG_XML).getroot()
    key = root.findtext("ApiKey")
    if not key:
        sys.exit(f"ApiKey not found in {WHISPARR_CONFIG_XML}")
    return key


WHISPARR_API_KEY = whisparr_api_key()


def request_json(method, url, api_key, payload=None):
    data = None
    headers = {
        "Accept": "application/json",
        "X-Api-Key": api_key,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc

    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def field_map(resource):
    return {
        field.get("name"): field.get("value")
        for field in resource.get("fields", [])
    }


def fields_match(resource, expected):
    actual = field_map(resource)
    for key, value in expected.items():
        if key == "apiKey" and actual.get(key) == "********":
            continue
        if actual.get(key) != value:
            return False
    return True


def normalize_mapped_categories(categories):
    normalized = []
    for item in categories or []:
        client_category = item.get("clientCategory")
        mapped_categories = item.get("categories") or []
        if not client_category:
            continue
        normalized.append(
            {
                "clientCategory": client_category,
                "categories": sorted(int(category) for category in mapped_categories),
            }
        )
    return sorted(
        normalized,
        key=lambda item: item["clientCategory"],
    )


def mapped_categories_match(resource, expected):
    actual = normalize_mapped_categories(resource.get("categories"))
    desired = normalize_mapped_categories(expected)
    return actual == desired


def ensure_whisparr_root_folder():
    rootfolders = request_json(
        "GET",
        f"{WHISPARR_URL}/api/v3/rootfolder",
        WHISPARR_API_KEY,
    )
    if any(folder.get("path") == WHISPARR_ROOT_FOLDER for folder in rootfolders):
        return False

    request_json(
        "POST",
        f"{WHISPARR_URL}/api/v3/rootfolder",
        WHISPARR_API_KEY,
        {"path": WHISPARR_ROOT_FOLDER},
    )
    return True


def ensure_whisparr_host_config():
    config = request_json(
        "GET",
        f"{WHISPARR_URL}/api/v3/config/host",
        WHISPARR_API_KEY,
    )
    expected = {
        "authenticationMethod": WHISPARR_AUTHENTICATION_METHOD,
        "authenticationRequired": WHISPARR_AUTHENTICATION_REQUIRED,
        "logLevel": WHISPARR_LOG_LEVEL,
        "consoleLogLevel": WHISPARR_CONSOLE_LOG_LEVEL,
    }
    if all(config.get(key) == value for key, value in expected.items()):
        return False

    config.update(expected)
    if config.get("password") is None:
        config["password"] = ""
    if config.get("passwordConfirmation") is None:
        config["passwordConfirmation"] = ""
    request_json(
        "PUT",
        f"{WHISPARR_URL}/api/v3/config/host",
        WHISPARR_API_KEY,
        config,
    )
    return True


def ensure_whisparr_indexer_config():
    config = request_json(
        "GET",
        f"{WHISPARR_URL}/api/v3/config/indexer",
        WHISPARR_API_KEY,
    )
    if all(config.get(key) == value for key, value in WHISPARR_INDEXER_CONFIG.items()):
        return False

    config.update(WHISPARR_INDEXER_CONFIG)
    request_json(
        "PUT",
        f"{WHISPARR_URL}/api/v3/config/indexer",
        WHISPARR_API_KEY,
        config,
    )
    return True


def whisparr_sabnzbd_payload(existing_id=None):
    fields = {
        "host": SABNZBD_HOST,
        "port": SABNZBD_PORT,
        "useSsl": False,
        "urlBase": "",
        "apiKey": SABNZBD_API_KEY,
        "username": "",
        "password": "",
        "movieCategory": SABNZBD_CATEGORY,
        "recentMoviePriority": -100,
        "olderMoviePriority": -100,
    }
    payload = {
        "enable": True,
        "protocol": "usenet",
        "priority": 1,
        "removeCompletedDownloads": True,
        "removeFailedDownloads": True,
        "name": "SABnzbd (Ansible)",
        "implementation": "Sabnzbd",
        "configContract": "SabnzbdSettings",
        "tags": [],
        "fields": [
            {"name": name, "value": value}
            for name, value in fields.items()
        ],
    }
    if existing_id is not None:
        payload["id"] = existing_id
    return payload, fields


def ensure_whisparr_download_client():
    clients = request_json(
        "GET",
        f"{WHISPARR_URL}/api/v3/downloadclient",
        WHISPARR_API_KEY,
    )
    existing = next(
        (
            client
            for client in clients
            if client.get("name") == "SABnzbd (Ansible)"
            or client.get("implementation") == "Sabnzbd"
        ),
        None,
    )

    if existing is None:
        payload, _ = whisparr_sabnzbd_payload()
        request_json(
            "POST",
            f"{WHISPARR_URL}/api/v3/downloadclient",
            WHISPARR_API_KEY,
            payload,
        )
        return True

    payload, expected_fields = whisparr_sabnzbd_payload(existing["id"])
    if (
        existing.get("name") == payload["name"]
        and existing.get("enable") is True
        and existing.get("protocol") == payload["protocol"]
        and fields_match(existing, expected_fields)
    ):
        return False

    request_json(
        "PUT",
        f"{WHISPARR_URL}/api/v3/downloadclient/{existing['id']}",
        WHISPARR_API_KEY,
        payload,
    )
    return True


def prowlarr_sabnzbd_payload(existing_id=None, existing=None):
    fields = {
        "host": SABNZBD_HOST,
        "port": SABNZBD_PORT,
        "useSsl": False,
        "urlBase": None,
        "apiKey": SABNZBD_API_KEY,
        "username": None,
        "password": None,
        "category": PROWLARR_DOWNLOAD_CLIENT_DEFAULT_CATEGORY,
        "priority": -100,
    }
    payload = {
        "enable": True,
        "protocol": "usenet",
        "priority": (existing or {}).get("priority", 1),
        "categories": PROWLARR_DOWNLOAD_CLIENT_MAPPED_CATEGORIES,
        "name": PROWLARR_DOWNLOAD_CLIENT_NAME,
        "implementation": "Sabnzbd",
        "configContract": "SabnzbdSettings",
        "tags": (existing or {}).get("tags", []),
        "fields": [
            {"name": name, "value": value}
            for name, value in fields.items()
        ],
    }
    if existing_id is not None:
        payload["id"] = existing_id
    return payload, fields


def ensure_prowlarr_download_client():
    clients = request_json(
        "GET",
        f"{PROWLARR_URL}/api/v1/downloadclient",
        PROWLARR_API_KEY,
    )
    existing = next(
        (
            client
            for client in clients
            if client.get("name") == PROWLARR_DOWNLOAD_CLIENT_NAME
            or client.get("implementation") == "Sabnzbd"
        ),
        None,
    )

    if existing is None:
        payload, _ = prowlarr_sabnzbd_payload()
        request_json(
            "POST",
            f"{PROWLARR_URL}/api/v1/downloadclient",
            PROWLARR_API_KEY,
            payload,
        )
        return True

    payload, expected_fields = prowlarr_sabnzbd_payload(existing["id"], existing)
    if (
        existing.get("name") == payload["name"]
        and existing.get("enable") is True
        and existing.get("protocol") == payload["protocol"]
        and fields_match(existing, expected_fields)
        and mapped_categories_match(existing, payload["categories"])
    ):
        return False

    request_json(
        "PUT",
        f"{PROWLARR_URL}/api/v1/downloadclient/{existing['id']}",
        PROWLARR_API_KEY,
        payload,
    )
    return True


def prowlarr_whisparr_payload(existing_id=None):
    fields = {
        "prowlarrUrl": PROWLARR_SELF_URL,
        "baseUrl": PROWLARR_WHISPARR_BASE_URL,
        "apiKey": WHISPARR_API_KEY,
        "syncCategories": PROWLARR_SYNC_CATEGORIES,
        "syncRejectBlocklistedTorrentHashesWhileGrabbing": False,
    }
    payload = {
        "name": "Whisparr",
        "syncLevel": "fullSync",
        "implementation": "Whisparr",
        "configContract": "WhisparrSettings",
        "tags": [],
        "fields": [
            {"name": name, "value": value}
            for name, value in fields.items()
        ],
    }
    if existing_id is not None:
        payload["id"] = existing_id
    return payload, fields


def ensure_prowlarr_application():
    apps = request_json(
        "GET",
        f"{PROWLARR_URL}/api/v1/applications",
        PROWLARR_API_KEY,
    )
    existing = next(
        (
            app
            for app in apps
            if app.get("name") == "Whisparr"
            or app.get("implementation") == "Whisparr"
        ),
        None,
    )

    if existing is None:
        payload, _ = prowlarr_whisparr_payload()
        request_json(
            "POST",
            f"{PROWLARR_URL}/api/v1/applications",
            PROWLARR_API_KEY,
            payload,
        )
        return True

    payload, expected_fields = prowlarr_whisparr_payload(existing["id"])
    if (
        existing.get("name") == payload["name"]
        and existing.get("syncLevel") == payload["syncLevel"]
        and fields_match(existing, expected_fields)
    ):
        return False

    request_json(
        "PUT",
        f"{PROWLARR_URL}/api/v1/applications/{existing['id']}",
        PROWLARR_API_KEY,
        payload,
    )
    return True


def main():
    changed = False
    changed |= ensure_whisparr_host_config()
    changed |= ensure_whisparr_indexer_config()
    changed |= ensure_whisparr_root_folder()
    changed |= ensure_whisparr_download_client()
    changed |= ensure_prowlarr_download_client()
    changed |= ensure_prowlarr_application()
    print("changed" if changed else "ok")


if __name__ == "__main__":
    main()
