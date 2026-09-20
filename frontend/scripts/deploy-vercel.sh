#!/usr/bin/env bash
#
# Deploy the GraphDrift frontend to Vercel, start to finish, in one sitting.
#
#   cd frontend && ./scripts/deploy-vercel.sh
#
# Run `vercel login` once beforehand; this script refuses to start otherwise.
#
# Why it deploys twice: the two services need each other's URL. The backend's
# ALLOWED_ORIGINS must name the Vercel origin or the browser blocks every API
# call, and VITE_API_BASE_URL is baked into the bundle at build time, so it
# cannot be filled in afterwards without rebuilding. Deploying the frontend
# first produces the origin Render needs, and the second deploy bakes in the
# backend URL Render hands back. The pause in the middle is where you create
# the Render Blueprint.
#
# Nothing here touches application code. It only sets project settings and
# environment variables on Vercel.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f vercel.json ]; then
  echo "Run this from the repository: frontend/vercel.json is missing." >&2
  exit 1
fi

if ! command -v vercel >/dev/null 2>&1; then
  echo "Vercel CLI not found. Install it with: npm i -g vercel" >&2
  exit 1
fi

if ! vercel whoami >/dev/null 2>&1; then
  echo "Not logged in to Vercel. Run: vercel login" >&2
  exit 1
fi

echo "==> Vercel CLI $(vercel --version 2>&1 | tail -1), logged in as $(vercel whoami 2>/dev/null)"

# ---------------------------------------------------------------- 1. link ----
echo
echo "==> Step 1/4: linking this directory to a Vercel project"
vercel link

# ------------------------------------------------- 2. first production run ----
echo
echo "==> Step 2/4: first production deploy, to find out the public origin"
echo "    The app will not reach its API yet. That is expected."
deploy_log="$(mktemp)"
trap 'rm -f "$deploy_log"' EXIT
vercel --prod 2>&1 | tee "$deploy_log"

# The CLI prints "Production: <url>" for a --prod deploy. That is the stable
# alias; the bare URL it also prints is the per-deployment one, which is not
# what ALLOWED_ORIGINS wants.
frontend_origin="$(sed -n 's|.*Production: *\(https://[A-Za-z0-9.-]*\).*|\1|p' "$deploy_log" | tail -1)"
if [ -z "$frontend_origin" ]; then
  echo
  echo "Could not read the production URL out of the CLI output above."
  read -r -p "Paste the production URL (https://...vercel.app): " frontend_origin
fi
frontend_origin="${frontend_origin%/}"
echo "==> Frontend origin: $frontend_origin"

# ------------------------------------------------------ 3. wait for Render ----
cat <<EOF

==> Step 3/4: create the backend, then come back here.

    1. https://dashboard.render.com  ->  New  ->  Blueprint
    2. Connect this GitHub repository. Render reads render.yaml by itself.
    3. It will prompt for exactly two values:

         ALLOWED_ORIGINS   $frontend_origin
         ADMIN_PASSWORD    a password you choose, at least 12 characters

    4. Wait for the service to report "Live", then copy its URL.

EOF
read -r -p "Backend URL (https://....onrender.com): " backend_origin
backend_origin="${backend_origin%/}"
case "$backend_origin" in
  https://*) ;;
  *) echo "Expected an https:// URL, got '$backend_origin'." >&2; exit 1 ;;
esac
ws_origin="wss://${backend_origin#https://}"

# --------------------------------------------- 4. env vars + real deploy ----
echo
echo "==> Step 4/4: setting production environment variables and redeploying"
set_env () {
  # Remove first so re-running this script is safe.
  vercel env rm "$1" production --yes >/dev/null 2>&1 || true
  printf '%s' "$2" | vercel env add "$1" production
  echo "    $1 = $2"
}
set_env VITE_API_BASE_URL "$backend_origin"
set_env VITE_WS_BASE_URL "$ws_origin"

vercel --prod

cat <<EOF

==> Done.

    Frontend  $frontend_origin
    Backend   $backend_origin

    Check the backend is up and not serving its schema:
      curl -i $backend_origin/health
      curl -i $backend_origin/openapi.json     # expect 404 in production

    Then sign in at $frontend_origin as 'admin' with the password you gave Render.

    If Render's URL is not what you pasted above, or the Vercel origin changes,
    both values are editable: VITE_* with this script (it is re-runnable), and
    ALLOWED_ORIGINS in the Render dashboard.
EOF
