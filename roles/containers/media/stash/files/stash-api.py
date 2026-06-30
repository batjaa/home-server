#!/usr/bin/env python3
"""Converge and scan Stash through its GraphQL API."""

import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = os.environ.get("STASH_URL", "http://127.0.0.1:9999").rstrip("/")
STASHES = json.loads(os.environ.get("STASH_STASHES", "[]"))
SCAN_PATHS = json.loads(os.environ.get("STASH_SCAN_PATHS", "[]"))
VIDEO_FILE_NAMING_ALGORITHM = os.environ.get(
    "STASH_VIDEO_FILE_NAMING_ALGORITHM",
    "OSHASH",
).upper()
SCAN_ON_CHANGE = os.environ.get("STASH_SCAN_ON_CHANGE", "true").lower() == "true"


def request_graphql(query, variables=None):
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    req = urllib.request.Request(
        f"{BASE_URL}/graphql",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"GraphQL request failed: {exc.code} {detail}") from exc

    result = json.loads(body.decode("utf-8"))
    if result.get("errors"):
        raise RuntimeError(json.dumps(result["errors"], indent=2))
    return result["data"]


def normalize_stashes(stashes):
    normalized = []
    for stash in stashes or []:
        normalized.append(
            {
                "path": stash["path"],
                "excludeVideo": bool(stash.get("excludeVideo", False)),
                "excludeImage": bool(stash.get("excludeImage", False)),
            }
        )
    return sorted(normalized, key=lambda item: item["path"])


def desired_stashes():
    normalized = []
    for stash in STASHES:
        normalized.append(
            {
                "path": stash["path"],
                "excludeVideo": bool(stash.get("excludeVideo", False)),
                "excludeImage": bool(stash.get("excludeImage", False)),
            }
        )
    return normalize_stashes(normalized)


def scan():
    if not SCAN_PATHS:
        print("scan skipped")
        return

    data = request_graphql(
        """
        mutation Scan($input: ScanMetadataInput!) {
          metadataScan(input: $input)
        }
        """,
        {
            "input": {
                "paths": SCAN_PATHS,
                "rescan": False,
                "scanGenerateCovers": False,
                "scanGeneratePreviews": False,
                "scanGenerateSprites": False,
                "scanGeneratePhashes": False,
                "scanGenerateThumbnails": False,
                "scanGenerateClipPreviews": False,
            }
        },
    )
    print(f"scan job {data['metadataScan']}")


def configure():
    data = request_graphql(
        """
        query Configuration {
          configuration {
            general {
              stashes {
                path
                excludeVideo
                excludeImage
              }
              videoFileNamingAlgorithm
            }
          }
        }
        """
    )
    general = data["configuration"]["general"]
    current_algorithm = (general.get("videoFileNamingAlgorithm") or "").upper()
    current_stashes = normalize_stashes(general.get("stashes"))
    next_stashes = desired_stashes()

    if (
        current_algorithm == VIDEO_FILE_NAMING_ALGORITHM
        and current_stashes == next_stashes
    ):
        print("ok")
        return

    request_graphql(
        """
        mutation Configure($input: ConfigGeneralInput!) {
          configureGeneral(input: $input) {
            videoFileNamingAlgorithm
            stashes {
              path
              excludeVideo
              excludeImage
            }
          }
        }
        """,
        {
            "input": {
                "videoFileNamingAlgorithm": VIDEO_FILE_NAMING_ALGORITHM,
                "stashes": next_stashes,
            }
        },
    )
    if SCAN_ON_CHANGE:
        scan()
    print("changed")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "configure"
    if command == "configure":
        configure()
    elif command == "scan":
        scan()
    else:
        sys.exit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
