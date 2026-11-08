#!/usr/bin/env python3
"""Workout edge planning. No network calls and no secret material."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


WORKOUT_FQDN = "workout.batjaa.site"
WORKOUT_CNAME_TARGET = "ddns.batjaa.site"
WORKOUT_SPLIT_IP = "192.168.50.20"
WORKOUT_RECORD_NAME = "workout"

_NAME_RE = re.compile(
    r"(?:^|[\s{,])name:\s*['\"]?workout['\"]?(?=$|[\s,}#])",
    re.M,
)
_TARGET_RE = re.compile(
    r"(?:^|[\s{,])content:\s*['\"]?ddns\.batjaa\.site\.?['\"]?(?=$|[\s,}#])",
    re.M,
)
_PROXIED_OFF_RE = re.compile(
    r"(?:^|[\s{,])proxied:\s*['\"]?(?:false|no|False)['\"]?(?=$|[\s,}#])",
    re.M,
)
_DOMAIN_RE = re.compile(
    r"(?:^|[\s{,])domain:\s*['\"]?workout\.batjaa\.site['\"]?(?=$|[\s,}#])",
    re.M,
)
_IP_RE = re.compile(
    r"(?:^|[\s{,])ip:\s*['\"]?192\.168\.50\.20['\"]?(?=$|[\s,}#])",
    re.M,
)
_KEY_RE = re.compile(r"^([ ]*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


def parse_pihole_hosts(raw: str) -> list[str]:
    """Extract the JSON string list from `pihole-FTL --config dns.hosts`."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("Pi-hole dns.hosts output is not a JSON list; nothing was written")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("Pi-hole dns.hosts output is not valid JSON; nothing was written") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("Pi-hole dns.hosts output is not a list of strings; nothing was written")
    return parsed


def merge_pihole_hosts(
    entries: list[str],
    domain: str = WORKOUT_FQDN,
    ip: str = WORKOUT_SPLIT_IP,
) -> tuple[list[str], bool]:
    """Return hosts with one workout row, leaving every other row untouched."""
    merged: list[str] = []
    replaced = False
    for entry in entries:
        parts = entry.split()
        if len(parts) >= 2 and parts[1] == domain:
            if not replaced:
                merged.append(f"{ip} {domain}")
                replaced = True
            continue
        merged.append(entry)
    if not replaced:
        merged.append(f"{ip} {domain}")
    return merged, merged != entries


def decide_cname(
    records: list[dict],
    fqdn: str = WORKOUT_FQDN,
    target: str = WORKOUT_CNAME_TARGET,
) -> dict:
    """Choose create, update, or none for the single workout CNAME.

    Records for other names are ignored. A non-CNAME or a second CNAME for
    this name is refused so the caller cannot convert or delete it.
    """
    if not isinstance(records, list):
        raise ValueError("Cloudflare result is not a list; nothing was changed")
    named = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Cloudflare result contains a non-object; nothing was changed")
        if record.get("name") == fqdn:
            named.append(record)
    others = [record for record in named if record.get("type") != "CNAME"]
    cnames = [record for record in named if record.get("type") == "CNAME"]
    if others:
        kinds = ", ".join(sorted({str(record.get("type")) for record in others}))
        raise ValueError(
            f"{fqdn} has non-CNAME record type {kinds}; refusing to change it"
        )
    if len(cnames) > 1:
        raise ValueError(f"{fqdn} has multiple CNAME records; refusing to choose one")

    if not cnames:
        return {
            "action": "create",
            "method": "POST",
            "body": {
                "type": "CNAME",
                "name": fqdn,
                "content": target,
                "proxied": False,
                "ttl": 1,
            },
        }

    current = cnames[0]
    previous = {
        "id": current.get("id"),
        "type": current.get("type"),
        "name": current.get("name"),
        "content": current.get("content"),
        "proxied": bool(current.get("proxied")),
        "ttl": current.get("ttl"),
    }
    if current.get("content") == target and not bool(current.get("proxied")):
        return {"action": "none", "previous": previous}
    if not current.get("id"):
        raise ValueError(f"{fqdn} CNAME has no id; refusing to update it")
    ttl = current.get("ttl")
    if not isinstance(ttl, int):
        ttl = 1
    return {
        "action": "update",
        "method": "PUT",
        "id": current["id"],
        "body": {
            "type": "CNAME",
            "name": fqdn,
            "content": target,
            "proxied": False,
            "ttl": ttl,
        },
        "previous": previous,
    }


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _leading_spaces(line: str) -> int:
    body = line.rstrip("\r\n")
    spaces = len(body) - len(body.lstrip(" "))
    remainder = body[spaces:]
    if remainder.startswith("\t"):
        raise ValueError("Tab indentation is not supported; the vars file was not changed")
    return spaces


def _find_key(lines: list[str], key: str) -> tuple[int, int, str]:
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        match = _KEY_RE.match(body)
        if match and match.group(2) == key:
            rest = match.group(3)
            if rest.startswith("#"):
                rest = ""
            return index, len(match.group(1)), rest
    raise ValueError(f"{key} was not found; the vars file was not changed")


def _block_end(lines: list[str], start: int, key_indent: int) -> int:
    for index in range(start + 1, len(lines)):
        body = lines[index].strip()
        if not body or body.startswith("#"):
            continue
        if _leading_spaces(lines[index]) <= key_indent:
            return index
    return len(lines)


def _items(lines: list[str], start: int, end: int) -> list[list[str]]:
    items: list[list[str]] = []
    current: list[str] | None = None
    current_indent = 0
    for index in range(start + 1, end):
        body = lines[index].strip()
        if not body or body.startswith("#"):
            continue
        indent = _leading_spaces(lines[index])
        content = lines[index].rstrip("\r\n")[indent:]
        if content.startswith("- ") or content == "-":
            if current is not None:
                items.append(current)
            current = [lines[index]]
            current_indent = indent
            continue
        if current is not None and indent > current_indent:
            current.append(lines[index])
    if current is not None:
        items.append(current)
    return items


def _insert_at(lines: list[str], start: int, end: int) -> int:
    last_dash = None
    for index in range(start + 1, end):
        body = lines[index].strip()
        if body.startswith("- ") or body == "-":
            last_dash = index
    if last_dash is None:
        return start + 1
    item_indent = _leading_spaces(lines[last_dash])
    index = last_dash + 1
    while index < end:
        body = lines[index].strip()
        if not body or body.startswith("#"):
            break
        if _leading_spaces(lines[index]) <= item_indent:
            break
        index += 1
    return index


def _style(items: list[list[str]]) -> tuple[str, int, int]:
    first = items[0][0]
    dash_indent = _leading_spaces(first)
    if "{" in first:
        return "flow", dash_indent, dash_indent + 2
    child_indent = dash_indent + 2
    for extra in items[0][1:]:
        if extra.strip() and not extra.strip().startswith("#"):
            child_indent = _leading_spaces(extra)
            break
    return "block", dash_indent, child_indent


def _cloudflare_ok(text: str) -> bool:
    return bool(_NAME_RE.search(text) and _TARGET_RE.search(text) and _PROXIED_OFF_RE.search(text))


def _pihole_ok(text: str) -> bool:
    return bool(_DOMAIN_RE.search(text) and _IP_RE.search(text))


def _cloudflare_lines(style: str, dash_indent: int, child_indent: int, nl: str) -> list[str]:
    if style == "flow":
        pad = " " * dash_indent
        return [
            f"{pad}- {{ name: workout, type: CNAME, content: ddns.batjaa.site, proxied: false }}{nl}"
        ]
    dash = " " * dash_indent
    child = " " * child_indent
    return [
        f"{dash}- name: workout{nl}",
        f"{child}type: CNAME{nl}",
        f"{child}content: ddns.batjaa.site{nl}",
        f"{child}proxied: false{nl}",
    ]


def _pihole_lines(style: str, dash_indent: int, child_indent: int, nl: str) -> list[str]:
    if style == "flow":
        pad = " " * dash_indent
        return [f"{pad}- {{ domain: workout.batjaa.site, ip: '192.168.50.20' }}{nl}"]
    dash = " " * dash_indent
    child = " " * child_indent
    return [
        f"{dash}- domain: workout.batjaa.site{nl}",
        f"{child}ip: '192.168.50.20'{nl}",
    ]


def _ensure_key(lines: list[str], key: str, nl: str, kind: str) -> tuple[list[str], bool]:
    start, key_indent, rest = _find_key(lines, key)
    if rest.startswith("["):
        blob = rest
        matched = _cloudflare_ok(blob) if kind == "cloudflare" else _pihole_ok(blob)
        present = bool(_NAME_RE.search(blob) if kind == "cloudflare" else _DOMAIN_RE.search(blob))
        if matched:
            return lines, False
        if present or rest != "[]":
            raise ValueError(
                f"{key} is an inline list that this apply will not rewrite"
            )
        key_line = f"{' ' * key_indent}{key}:{nl}"
        item = (
            _cloudflare_lines("flow", key_indent + 2, key_indent + 4, nl)
            if kind == "cloudflare"
            else _pihole_lines("flow", key_indent + 2, key_indent + 4, nl)
        )
        return lines[:start] + [key_line] + item + lines[start + 1 :], True

    if rest:
        raise ValueError(f"{key} uses an unsupported value; the vars file was not changed")

    end = _block_end(lines, start, key_indent)
    items = _items(lines, start, end)
    if items:
        style, dash_indent, child_indent = _style(items)
    else:
        style, dash_indent, child_indent = "flow", key_indent + 2, key_indent + 4

    for item in items:
        blob = "".join(item)
        if kind == "cloudflare" and _NAME_RE.search(blob):
            if _cloudflare_ok(blob):
                return lines, False
            raise ValueError(
                "cloudflare_records already lists workout with different values; "
                "the vars file was not changed"
            )
        if kind == "pihole" and _DOMAIN_RE.search(blob):
            if _pihole_ok(blob):
                return lines, False
            raise ValueError(
                "pihole_split_dns already lists workout.batjaa.site with a different address; "
                "the vars file was not changed"
            )

    new_lines = (
        _cloudflare_lines(style, dash_indent, child_indent, nl)
        if kind == "cloudflare"
        else _pihole_lines(style, dash_indent, child_indent, nl)
    )
    insert = _insert_at(lines, start, end)
    return lines[:insert] + new_lines + lines[insert:], True


def ensure_host_vars_text(text: str) -> tuple[str, bool]:
    """Insert the workout CNAME and split-DNS rows, preserving every other line."""
    nl = _newline(text)
    lines = text.splitlines(keepends=True)
    # Confirm both keys before either list is edited.
    _find_key(lines, "cloudflare_records")
    _find_key(lines, "pihole_split_dns")
    lines, cloudflare_changed = _ensure_key(lines, "cloudflare_records", nl, "cloudflare")
    lines, pihole_changed = _ensure_key(lines, "pihole_split_dns", nl, "pihole")
    if not cloudflare_changed and not pihole_changed:
        return text, False
    return "".join(lines), True


def ensure_host_vars_file(path: Path) -> str:
    original = path.read_text()
    updated, changed = ensure_host_vars_text(original)
    if not changed:
        return "UNCHANGED"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.workout-edge-{stamp}.bak")
    temporary = path.with_name(f".{path.name}.workout-edge.tmp")
    shutil.copy2(path, backup)
    try:
        temporary.write_text(updated)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return "CHANGED"


def preview_host_vars_file(path: Path) -> str:
    _updated, changed = ensure_host_vars_text(path.read_text())
    return "CHANGED" if changed else "UNCHANGED"


def _print_status(status: str) -> int:
    sys.stdout.write(status + "\n")
    return 0


def main(argv: list[str]) -> int:
    try:
        command = argv[1]
        if command == "ensure-host-vars":
            return _print_status(ensure_host_vars_file(Path(argv[2])))
        if command == "preview-host-vars":
            return _print_status(preview_host_vars_file(Path(argv[2])))
        if command == "merge-pihole":
            output = Path(argv[2])
            domain = argv[3]
            address = argv[4]
            merged, changed = merge_pihole_hosts(
                parse_pihole_hosts(sys.stdin.read()),
                domain,
                address,
            )
            # Always leave the merged list for the following task. An unchanged
            # plan is still a file so a skipped write does not trip over a
            # missing path while Ansible templates arguments.
            output.write_text(json.dumps(merged))
            os.chmod(output, 0o600)
            return _print_status("CHANGED" if changed else "UNCHANGED")
        if command == "decide-cname":
            plan = decide_cname(json.load(sys.stdin), argv[2], argv[3])
            sys.stdout.write(json.dumps(plan, separators=(",", ":")) + "\n")
            return 0
    except (ValueError, IndexError, json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stderr.write(
        "usage: workout_edge.py ensure-host-vars|preview-host-vars|merge-pihole|decide-cname ...\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
