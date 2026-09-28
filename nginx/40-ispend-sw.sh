#!/bin/sh
# Stamps the service worker with this deploy's app shell. Run by the nginx image's entrypoint
# (mounted into /docker-entrypoint.d/) on every container start; install.sh's Update recreates the
# container, so each deploy gets a new build id and phones swap to the new shell in one step.
#
# Writes $OUT from $HTML/sw.js with two placeholders filled in:
#   __ISPEND_BUILD__        content hash of every precached file (+ the worker itself)
#   [/*__ISPEND_PRECACHE__*/]  JSON list of the files the worker precaches and serves cache-first
# ISPEND_SW_CACHE=off (docker-compose.dev.yml) stamps build "dev": the worker caches nothing, so files
# edited live in ./frontend are never served stale. Safe to re-run at any time (qa/e2e/test_pwa.py does).
set -eu

HTML="${ISPEND_HTML_ROOT:-/usr/share/nginx/html}"
OUT="${ISPEND_SW_OUT:-/var/cache/ispend/sw.js}"
mkdir -p "$(dirname "$OUT")"

if [ "${ISPEND_SW_CACHE:-on}" = "off" ]; then
    build="dev"
    list="[]"
else
    # The signed-in app, its auth pages and the offline page. The public marketing pages (landing,
    # privacy, terms) and their fonts stay network-only; the full Inter only loads for non-Latin text.
    files=$(cd "$HTML" && {
        ls ./*.html
        find ./css ./js ./img -type f \( -name '*.css' -o -name '*.js' -o -name '*.svg' -o -name '*.png' \)
        printf '%s\n' ./favicon.svg ./manifest.json ./manifest-admin.json ./fonts/InterVariable-latin-v2.woff2 ./vendor/chart.umd.js
    } | sed 's|^\./|/|' | grep -v -E '^/(landing|privacy|terms)\.html$|^/(css|js)/pages/landing\.|^/sw\.js$' | LC_ALL=C sort -u)
    missing=""
    for f in $files; do [ -f "$HTML$f" ] || missing="$missing $f"; done
    if [ -n "$missing" ]; then
        # Never keep nginx from starting: an unstamped worker simply caches nothing.
        echo "40-ispend-sw: WARNING missing shell files:$missing — serving the worker unstamped" >&2
        build="__ISPEND_BUILD__"
        list="[]"
    else
        build=$(cd "$HTML" && { for f in $files; do cat ".$f"; done; cat sw.js; } | sha1sum | cut -c1-12)
        list="[$(printf '%s\n' $files | sed 's|.*|"&"|' | paste -sd, -)]"
    fi
fi

tmp="$OUT.tmp"
sed -e "s|__ISPEND_BUILD__|$build|" -e "s|\[/\*__ISPEND_PRECACHE__\*/\]|$list|" "$HTML/sw.js" > "$tmp"
mv "$tmp" "$OUT"
echo "40-ispend-sw: service worker build $build"
