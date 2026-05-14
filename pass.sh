#!/usr/bin/env bash
# Ansible vault password — read from 1Password via the `op` CLI.
# Falls back to macOS Keychain if `op` is unavailable, so a fresh shell
# without 1Password installed can still operate in an emergency.

set -euo pipefail

if command -v op >/dev/null 2>&1; then
  exec op read "op://Private/Homeserver Ansible Vault Key/password"
fi

exec /usr/bin/security find-generic-password -w -a "batjaa" -l "ansible-vault-password"
