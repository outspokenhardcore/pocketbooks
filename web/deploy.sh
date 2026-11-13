#!/usr/bin/env bash
#
# Upload the PocketBooks landing page to the mauli.app document root as the
# /pocketbooks subdirectory.
#
# The page is plain static HTML, so there is nothing to build. Every
# connection detail comes from the environment, so this public repository
# never contains a hostname, user, path, or key. Keep them in a file outside
# the repo, for example ~/.config/pocketbooks/web.env, and source it first.
#
#   export KB_WEB_SSH_HOST=your-server.example
#   export KB_WEB_SSH_USER=youruser
#   export KB_WEB_SSH_PORT=22
#   export KB_WEB_REMOTE_DIR=/path/to/docroot/pocketbooks
#   export KB_WEB_SSH_KEY=~/.ssh/id_ed25519     # optional
#   web/deploy.sh
#
set -euo pipefail

cd "$(dirname "$0")"

: "${KB_WEB_SSH_HOST:?SSH host of the web server}"
: "${KB_WEB_SSH_USER:?SSH user}"
: "${KB_WEB_REMOTE_DIR:?Absolute path of the /pocketbooks folder in the document root}"
KB_WEB_SSH_PORT="${KB_WEB_SSH_PORT:-22}"

SITE="site"
[ -f "${SITE}/index.html" ] || { echo "${SITE}/index.html is missing" >&2; exit 1; }

# A page served from a subdirectory must not reference the domain root.
if grep -qE '(src|href)="/[^/]' "${SITE}/index.html"; then
  echo "${SITE}/index.html references absolute paths; they break under" >&2
  echo "/pocketbooks/. Use relative paths such as assets/icon-180.png." >&2
  exit 1
fi

missing=0
while read -r ref; do
  [ -e "${SITE}/${ref}" ] || { echo "missing asset: ${ref}" >&2; missing=1; }
done < <(grep -oE '(src|href)="[^":]+"' "${SITE}/index.html" \
         | sed -E 's/^[a-z]+="//; s/"$//' | grep -v '^#' | sort -u)
[ "${missing}" -eq 0 ] || exit 1

ssh_opts=(-p "${KB_WEB_SSH_PORT}")
[ -n "${KB_WEB_SSH_KEY:-}" ] && ssh_opts+=(-i "${KB_WEB_SSH_KEY}")

echo "==> Uploading ${SITE}/ to ${KB_WEB_SSH_HOST}:${KB_WEB_REMOTE_DIR}"
ssh "${ssh_opts[@]}" "${KB_WEB_SSH_USER}@${KB_WEB_SSH_HOST}" \
  "mkdir -p '${KB_WEB_REMOTE_DIR}'"

rsync -az --delete --exclude='/catalogue/' --human-readable \
  -e "ssh ${ssh_opts[*]}" \
  "${SITE}/" "${KB_WEB_SSH_USER}@${KB_WEB_SSH_HOST}:${KB_WEB_REMOTE_DIR}/"

echo
echo "Done. Check https://www.mauli.app/pocketbooks/ — the page should render"
echo "styled, with all five screenshots and the EN/DE toggle working."
