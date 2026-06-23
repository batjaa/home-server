#!/usr/bin/env bash
# Onboard a GitHub repo to the Hermes autonomous coding pipeline.
#
#   scripts/hermes-onboard-repo.sh <owner/repo>
#
# Idempotent. Does everything needed so `/handoff` works from a clone of the repo:
#   1. invite the bot (hermes-bot-batjaa) as a push collaborator
#   2. auto-accept the invite using the bot's own token (from the Hermes container)
#   3. branch-protect main (require PR + 1 review; admins bypass for hotfixes)
#   4. clone the repo into Hermes' workspace + chown to the worker uid (1001)
#   5. create a kanban board for it + bind the workspace
#   6. install the auto-PR workflow (scripts/hermes-auto-pr.yml) into the repo
set -euo pipefail

REPO="${1:?usage: hermes-onboard-repo.sh <owner/repo>}"
NAME="${REPO##*/}"
BOT="hermes-bot-batjaa"
SSH="ssh -p 100 -o ConnectTimeout=15 batjaa@192.168.50.20"
CWS="/opt/data/workspaces/${NAME}"                 # workspace path inside the container

echo "==> [1/5] invite $BOT to $REPO (push)"
gh api -X PUT "repos/$REPO/collaborators/$BOT" -f permission=push >/dev/null || true

echo "==> [2/5] auto-accept invite as the bot"
BOT_TOKEN="$($SSH "docker exec hermes printenv GITHUB_TOKEN" | tr -d '\r')"
INV_ID="$(GH_TOKEN="$BOT_TOKEN" gh api /user/repository_invitations \
            --jq ".[] | select(.repository.full_name==\"$REPO\") | .id" 2>/dev/null | head -1)"
if [ -n "${INV_ID:-}" ]; then
  GH_TOKEN="$BOT_TOKEN" gh api --method PATCH "/user/repository_invitations/$INV_ID" >/dev/null
  echo "    accepted invite $INV_ID"
else
  echo "    no pending invite (already a collaborator)"
fi

echo "==> [3/5] branch protection on main"
gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null <<'JSON'
{"required_status_checks":null,"enforce_admins":false,"required_pull_request_reviews":{"required_approving_review_count":1,"dismiss_stale_reviews":true,"require_code_owner_reviews":false},"restrictions":null}
JSON

echo "==> [4/5] clone into Hermes workspace + chown to worker uid"
$SSH "docker exec hermes sh -c 'rm -rf $CWS && git clone https://x-access-token:\$GITHUB_TOKEN@github.com/$REPO.git $CWS && cd $CWS && git config user.name $BOT && git config user.email ${BOT}@users.noreply.github.com' && docker exec hermes chown -R 1001:1001 $CWS"

echo "==> [5/5] create kanban board '$NAME' + bind workspace"
$SSH "docker exec hermes hermes kanban boards create $NAME 2>/dev/null || true; docker exec hermes hermes kanban boards set-default-workdir $NAME $CWS"

echo "==> done: '$REPO' onboarded → board '$NAME'. Run /handoff from a clone of it."
echo "    PRs are opened by the worker itself via /opt/data/bin/open-pr (deployed by the hermes role)."
