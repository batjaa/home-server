#!/usr/bin/env bash
#
# Sync ansible-vault-encrypted host_vars/<host>/secret.yml files to 1Password
# as Document items. The plaintext repo is public and these files are
# gitignored, so 1Password is their only offsite backup.
#
# Subcommands:
#   edit <secret.yml>   ansible-vault edit + auto-push on save
#   push [host]         upload local secret.yml(s) to 1P (default: all hosts)
#   pull [host]         download from 1P, overwriting local (default: all hosts)
#   status              compare local vs 1P, report drift per host
#
# Requires: `op` signed in to your 1Password account.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_NAME="Private"
TITLE_PREFIX="Homeserver Vault"

discover_hosts() {
  cd "$REPO_ROOT"
  for f in host_vars/*/secret.yml; do
    [ -f "$f" ] || continue
    basename "$(dirname "$f")"
  done
}

item_title() { printf '%s — %s' "$TITLE_PREFIX" "$1"; }
secret_path() { printf '%s/host_vars/%s/secret.yml' "$REPO_ROOT" "$1"; }

item_exists() {
  op item get "$(item_title "$1")" --vault "$VAULT_NAME" >/dev/null 2>&1
}

push_one() {
  local host="$1" path title
  path="$(secret_path "$host")"
  title="$(item_title "$host")"
  if [ ! -f "$path" ]; then
    echo "skip $host (no local secret.yml)"
    return
  fi
  if item_exists "$host"; then
    op document edit "$title" "$path" --vault "$VAULT_NAME" --file-name "secret.yml" >/dev/null
    echo "pushed $host (updated)"
  else
    op document create "$path" --title "$title" --vault "$VAULT_NAME" --file-name "secret.yml" >/dev/null
    echo "pushed $host (created)"
  fi
}

pull_one() {
  local host="$1" path title
  path="$(secret_path "$host")"
  title="$(item_title "$host")"
  if ! item_exists "$host"; then
    echo "skip $host (no 1P item)"
    return
  fi
  mkdir -p "$(dirname "$path")"
  op document get "$title" --vault "$VAULT_NAME" --out-file "$path" --force >/dev/null
  chmod 600 "$path"
  echo "pulled $host -> $path"
}

status_one() {
  local host="$1" path title local_hash remote_hash
  path="$(secret_path "$host")"
  title="$(item_title "$host")"
  if [ ! -f "$path" ] && ! item_exists "$host"; then
    echo "$host: missing both sides"
    return
  fi
  if [ ! -f "$path" ]; then
    echo "$host: LOCAL MISSING (1P has it — run: $0 pull $host)"
    return
  fi
  if ! item_exists "$host"; then
    echo "$host: 1P MISSING (local has it — run: $0 push $host)"
    return
  fi
  local_hash=$(shasum -a 256 "$path" | awk '{print $1}')
  remote_hash=$(op document get "$title" --vault "$VAULT_NAME" | shasum -a 256 | awk '{print $1}')
  if [ "$local_hash" = "$remote_hash" ]; then
    echo "$host: in sync"
  else
    echo "$host: DRIFT — local != 1P (run: $0 push $host  or  $0 pull $host)"
  fi
}

usage() {
  cat <<USAGE
usage: $0 <command> [args]
  edit <secret.yml>   ansible-vault edit + auto-push to 1P
  push [host]         upload local secret.yml(s) to 1P
  pull [host]         download from 1P, overwrites local
  status              show sync state per host
USAGE
  exit 2
}

cmd="${1:-}"; shift || true

case "$cmd" in
  edit)
    [ $# -ge 1 ] || usage
    target="$1"
    [ -f "$target" ] || { echo "no such file: $target"; exit 1; }
    host="$(basename "$(dirname "$target")")"
    before_hash=$(shasum -a 256 "$target" | awk '{print $1}')
    ansible-vault edit "$target"
    after_hash=$(shasum -a 256 "$target" | awk '{print $1}')
    if [ "$before_hash" = "$after_hash" ]; then
      echo "$host: no changes, skipping push"
    else
      push_one "$host"
    fi
    ;;
  push)
    if [ $# -ge 1 ]; then push_one "$1"; else for h in $(discover_hosts); do push_one "$h"; done; fi
    ;;
  pull)
    if [ $# -ge 1 ]; then pull_one "$1"; else for h in $(discover_hosts); do pull_one "$h"; done; fi
    ;;
  status)
    for h in $(discover_hosts); do status_one "$h"; done
    ;;
  *)
    usage
    ;;
esac
