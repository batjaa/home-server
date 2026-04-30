#!/usr/bin/env python3
"""merge-servers — splice an Ansible-rendered [servers] block into sabnzbd.ini.

Reads /opt/docker/data/sabnzbd/sabnzbd.ini, finds the existing [servers]
section (and its [[<name>]] sub-blocks), replaces it with the contents of
the fragment file passed as argv[1], writes back atomically.

Exits 0; prints "changed" to stdout if the file was actually modified
(used by the Ansible task's changed_when).
"""

import os
import shutil
import sys
import tempfile


def find_top_section(lines, start):
    """Return index of next top-level section ([X], not [[X]]) after `start`."""
    for i in range(start + 1, len(lines)):
        s = lines[i].lstrip()
        if s.startswith("[") and not s.startswith("[["):
            return i
    return len(lines)


def main(ini_path: str, fragment_path: str) -> int:
    with open(ini_path) as f:
        lines = f.readlines()
    with open(fragment_path) as f:
        new_block = f.read()
    if not new_block.endswith("\n"):
        new_block += "\n"

    start = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("[servers]")),
        None,
    )
    if start is None:
        print("ERROR: [servers] section not found in sabnzbd.ini", file=sys.stderr)
        return 2

    end = find_top_section(lines, start)
    new_lines = lines[:start] + [new_block] + lines[end:]
    new_content = "".join(new_lines)
    old_content = "".join(lines)

    if new_content == old_content:
        return 0  # No change.

    fd, tmp = tempfile.mkstemp(prefix=".sabnzbd.ini.", dir=os.path.dirname(ini_path))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_content)
        shutil.copymode(ini_path, tmp)
        os.replace(tmp, ini_path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    print("changed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <sabnzbd.ini> <servers-fragment>", file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
