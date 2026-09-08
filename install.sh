#!/usr/bin/env bash
#
# iSpend — all-in-one installer for Ubuntu 22.04 / 24.04 LTS
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/ruolez/ispend/main/install.sh)"
#
# Menu: Install (with optional HTTPS via Let's Encrypt) · Update (pull, rebuild, keep
# every byte of data and the certificate) · SSL only (install or repair HTTPS) · Remove
# Non-interactive: install.sh install|update|ssl|remove
#
set -euo pipefail

REPO_URL="https://github.com/ruolez/ispend.git"
INSTALL_DIR="/opt/ispend"
BACKUP_DIR="/opt/ispend-backups"
DEFAULT_HTTP_PORT="80"
DEFAULT_HTTPS_PORT="443"
RENEW_HOOK="/etc/letsencrypt/renewal-hooks/deploy/ispend-reload.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

require_root() { [ "$(id -u)" -eq 0 ] || fail "Run this script as root: sudo bash install.sh"; }

compose() { docker compose --project-directory "$INSTALL_DIR" "$@"; }

get_env() { grep -E "^$1=" "$INSTALL_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true; }

set_env() {
    local key="$1" val="$2" file="$INSTALL_DIR/.env"
    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        echo "${key}=${val}" >> "$file"
    fi
}

unset_env() { sed -i "/^$1=/d" "$INSTALL_DIR/.env" 2>/dev/null || true; }

server_ip() { hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost"; }

# ---------------------------------------------------------------- resources
# Scale per-container memory limits to the host's RAM (written to .env, read by
# docker-compose.yml). The backend gets the larger share: OCR runs there.
configure_resources() {
    local total reserve budget nginx remaining postgres backend shared eff
    total="$(awk '/^MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
    if [ -z "$total" ] || [ "$total" -le 0 ]; then
        warn "Could not detect host RAM — keeping default resource limits."
        return
    fi
    reserve=$(( total / 5 )); [ "$reserve" -lt 768 ] && reserve=768
    budget=$(( total - reserve )); [ "$budget" -lt 512 ] && budget=512
    nginx=128
    remaining=$(( budget - nginx ))
    postgres=$(( remaining * 40 / 100 ))
    backend=$(( remaining - postgres ))
    [ "$postgres" -lt 256 ] && postgres=256
    [ "$backend" -lt 512 ] && backend=512
    shared=$(( postgres / 4 ));   [ "$shared" -lt 128 ] && shared=128
    eff=$(( postgres * 60 / 100 )); [ "$eff" -lt 256 ] && eff=256
    set_env NGINX_MEM_LIMIT    "${nginx}M"
    set_env BACKEND_MEM_LIMIT  "${backend}M"
    set_env POSTGRES_MEM_LIMIT "${postgres}M"
    set_env PG_SHARED_BUFFERS  "${shared}MB"
    set_env PG_EFFECTIVE_CACHE "${eff}MB"
    ok "Resource limits for ${total} MB host: backend=${backend}M, postgres=${postgres}M, nginx=${nginx}M"
}

# ---------------------------------------------------------------- docker / certbot
install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        ok "Docker with Compose already installed"
        return
    fi
    info "Installing Docker (official repository)..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    ok "Docker installed"
}

install_certbot() {
    if command -v certbot >/dev/null 2>&1; then ok "certbot already installed"; return; fi
    info "Installing certbot..."
    apt-get update -qq
    apt-get install -y -qq certbot
    ok "certbot installed"
}

# ---------------------------------------------------------------- health / backup
wait_for_health() {
    local url="$1"
    info "Waiting for the application to become healthy..."
    for _ in $(seq 1 60); do
        if curl -fsSk "${url}/api/health" >/dev/null 2>&1; then ok "Application is up"; return 0; fi
        sleep 2
    done
    warn "Health check timed out. Inspect logs with: docker compose --project-directory $INSTALL_DIR logs"
    return 1
}

app_url() {
    local domain http https
    domain="$(get_env DOMAIN)"; http="$(get_env APP_PORT)"; https="$(get_env APP_HTTPS_PORT)"
    if [ -n "$domain" ] && [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]; then
        [ "${https:-443}" = "443" ] && echo "https://${domain}" || echo "https://${domain}:${https}"
    else
        [ "${http:-80}" = "80" ] && echo "http://$(server_ip)" || echo "http://$(server_ip):${http}"
    fi
}

backup_data() {
    if ! compose ps postgres 2>/dev/null | grep -q "Up"; then
        warn "Postgres container is not running — skipping backup"
        return
    fi
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    local old_umask; old_umask="$(umask)"
    umask 077   # dumps hold every user's transactions: owner-readable only
    local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
    local dump="$BACKUP_DIR/ispend-db-$stamp.sql.gz"
    info "Backing up database to $dump ..."
    compose exec -T postgres pg_dump -U ispend ispend | gzip > "$dump"
    info "Backing up uploaded statements..."
    docker run --rm -v ispend_statements:/data:ro -v "$BACKUP_DIR":/out alpine \
        tar czf "/out/ispend-statements-$stamp.tgz" --exclude='./_backups' -C /data . 2>/dev/null || warn "Statement volume backup skipped"
    cp "$INSTALL_DIR/.env" "$BACKUP_DIR/env-$stamp" 2>/dev/null || true
    ls -1t "$BACKUP_DIR"/ispend-db-*.sql.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
    ls -1t "$BACKUP_DIR"/ispend-statements-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm -f
    umask "$old_umask"
    ok "Backup complete ($(du -h "$dump" | cut -f1)); the last 10 backups are kept"
}

# ---------------------------------------------------------------- SSL
valid_domain() { [[ "$1" =~ ^([a-zA-Z0-9](-*[a-zA-Z0-9])*\.)+[a-zA-Z]{2,}$ ]]; }

prompt_domain() {
    local domain current
    current="$(get_env DOMAIN 2>/dev/null || true)"
    while true; do
        read -r -p "Domain name for HTTPS (e.g. finance.example.com)${current:+ [$current]}: " domain
        domain="${domain:-$current}"
        domain="${domain#https://}"; domain="${domain#http://}"; domain="${domain%%/*}"
        if valid_domain "$domain"; then break; fi
        warn "That does not look like a domain name."
    done
    local resolved; resolved="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | head -1 || true)"
    local mine; mine="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || server_ip)"
    if [ -z "$resolved" ]; then
        warn "$domain does not resolve yet. Create an A record pointing to ${mine} before continuing."
        read -r -p "Continue anyway? [y/N] " answer
        case "${answer:-N}" in [Yy]*) ;; *) fail "Aborted." ;; esac
    elif [ "$resolved" != "$mine" ]; then
        warn "$domain resolves to $resolved but this server's public IP looks like $mine."
        read -r -p "Continue anyway? [y/N] " answer
        case "${answer:-N}" in [Yy]*) ;; *) fail "Aborted." ;; esac
    else
        ok "$domain points at this server"
    fi
    DOMAIN="$domain"
}

prompt_email() {
    local email current
    current="$(get_env LETSENCRYPT_EMAIL 2>/dev/null || true)"
    read -r -p "Email for Let's Encrypt expiry notices (optional)${current:+ [$current]}: " email
    LE_EMAIL="${email:-$current}"
}

# Render nginx/nginx.ssl.conf from the template for the configured domain.
render_ssl_conf() {
    local domain="$1"
    sed "s/__DOMAIN__/${domain}/g" "$INSTALL_DIR/nginx/nginx.ssl.conf.template" > "$INSTALL_DIR/nginx/nginx.ssl.conf"
}

install_renew_hook() {
    mkdir -p "$(dirname "$RENEW_HOOK")"
    cat > "$RENEW_HOOK" <<EOF
#!/usr/bin/env bash
# Reload nginx inside the iSpend stack after certbot renews the certificate.
docker compose --project-directory "$INSTALL_DIR" exec -T nginx nginx -s reload >/dev/null 2>&1 || true
EOF
    chmod +x "$RENEW_HOOK"
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
    ok "Automatic renewal enabled (certbot.timer, nginx reloads via deploy hook)"
}

# Issue the certificate through the ACME webroot served by nginx on port 80.
obtain_certificate() {
    local domain="$1" email="$2"
    mkdir -p "$INSTALL_DIR/certbot-www"
    if [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]; then
        ok "A certificate for $domain already exists — reusing it"
        return 0
    fi
    info "Starting nginx in challenge mode on port 80..."
    set_env NGINX_CONF "./nginx/nginx.acme.conf"
    set_env COMPOSE_FILE "docker-compose.yml:docker-compose.ssl.yml"
    render_ssl_conf "$domain"   # the overlay mounts /etc/letsencrypt even before it holds a cert
    compose up -d --no-deps --force-recreate nginx
    sleep 2
    local email_args=(--register-unsafely-without-email)
    [ -n "$email" ] && email_args=(--email "$email" --no-eff-email)
    info "Requesting a Let's Encrypt certificate for $domain ..."
    if ! certbot certonly --webroot -w "$INSTALL_DIR/certbot-www" -d "$domain" \
            --agree-tos --non-interactive --keep-until-expiring "${email_args[@]}"; then
        warn "Certificate request failed. Common causes: DNS not pointing here yet, port 80 blocked by a firewall."
        return 1
    fi
    ok "Certificate issued for $domain"
}

enable_ssl() {
    local domain="$1"
    render_ssl_conf "$domain"
    set_env DOMAIN "$domain"
    set_env NGINX_CONF "./nginx/nginx.ssl.conf"
    set_env COMPOSE_FILE "docker-compose.yml:docker-compose.ssl.yml"
    set_env SESSION_COOKIE_SECURE "1"
    grep -q '^APP_HTTPS_PORT=' "$INSTALL_DIR/.env" || set_env APP_HTTPS_PORT "$DEFAULT_HTTPS_PORT"
    install_renew_hook
}

disable_ssl_env() {
    unset_env COMPOSE_FILE
    unset_env NGINX_CONF
    set_env SESSION_COOKIE_SECURE "0"
}

# Keep the rendered SSL config in step with the template after an update; certificates are never touched.
refresh_ssl_conf() {
    local domain; domain="$(get_env DOMAIN)"
    [ -n "$domain" ] || return 0
    if [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]; then
        render_ssl_conf "$domain"
        install_renew_hook
        ok "HTTPS configuration for $domain refreshed (certificate reused)"
    else
        warn "DOMAIN=$domain is set but no certificate exists — run the SSL option to issue one."
    fi
}

firewall_hint() {
    if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
        warn "ufw is active. Allow the app ports with: ufw allow ${1}/tcp${2:+ && ufw allow ${2}/tcp}"
    fi
}

# ---------------------------------------------------------------- install
do_install() {
    if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
        warn "iSpend is already installed at $INSTALL_DIR."
        read -r -p "Run an UPDATE instead? [Y/n] " answer
        case "${answer:-Y}" in [Yy]*) do_update; return ;; *) fail "Aborted." ;; esac
    fi

    info "Installing prerequisites..."
    apt-get update -qq
    apt-get install -y -qq git curl openssl ca-certificates
    install_docker

    info "Cloning repository..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"

    local use_ssl="n" http_port https_port admin_pass
    read -r -p "Serve over HTTPS with a Let's Encrypt certificate? [Y/n] " answer
    case "${answer:-Y}" in [Yy]*) use_ssl="y" ;; esac

    if [ "$use_ssl" = "y" ]; then
        prompt_domain
        prompt_email
        http_port="$DEFAULT_HTTP_PORT"; https_port="$DEFAULT_HTTPS_PORT"
    else
        read -r -p "Port to expose the app on [${DEFAULT_HTTP_PORT}]: " http_port
        http_port="${http_port:-$DEFAULT_HTTP_PORT}"
    fi
    admin_pass="$(openssl rand -hex 6)"

    info "Generating .env with random secrets..."
    cat > "$INSTALL_DIR/.env" <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 16)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_INITIAL_PASSWORD=${admin_pass}
APP_PORT=${http_port}
EOF
    chmod 600 "$INSTALL_DIR/.env"
    [ "$use_ssl" = "y" ] && { set_env APP_HTTPS_PORT "$https_port"; set_env LETSENCRYPT_EMAIL "${LE_EMAIL:-}"; }

    info "Tuning container resource limits to this host..."
    configure_resources

    info "Building images (first build takes a few minutes; it installs the OCR engine)..."
    compose build

    if [ "$use_ssl" = "y" ]; then
        install_certbot
        if obtain_certificate "$DOMAIN" "${LE_EMAIL:-}"; then
            enable_ssl "$DOMAIN"
        else
            warn "Falling back to plain HTTP on port ${http_port}. Run the SSL option later to retry."
            disable_ssl_env
            set_env DOMAIN "$DOMAIN"
        fi
    fi

    info "Starting containers (database migrations run automatically at startup)..."
    compose up -d --force-recreate
    wait_for_health "$(app_url)" || true
    firewall_hint "$http_port" "${https_port:-}"

    echo
    ok "iSpend installed."
    echo -e "  URL:       ${GREEN}$(app_url)${NC}"
    echo -e "  Username:  ${GREEN}admin${NC}"
    echo -e "  Password:  ${GREEN}${admin_pass}${NC}   (autogenerated — change it in Settings; also saved in ${INSTALL_DIR}/.env)"
    echo -e "  Next:      Settings → Accounts, then Import a statement. AI suggestions are optional (Settings → AI)."
}

# ---------------------------------------------------------------- update
do_update() {
    [ -f "$INSTALL_DIR/docker-compose.yml" ] || fail "No installation found at $INSTALL_DIR — run Install first."
    cd "$INSTALL_DIR"

    backup_data

    info "Pulling latest code..."
    git -C "$INSTALL_DIR" fetch origin main
    git -C "$INSTALL_DIR" reset --hard origin/main

    info "Re-tuning container resource limits to this host..."
    configure_resources
    refresh_ssl_conf

    # Build the new images while the current stack keeps serving, then swap.
    info "Building updated images..."
    compose build
    info "Restarting with the new build (database, statements, users, settings and certificates are kept; migrations apply on startup)..."
    compose up -d --force-recreate

    info "Cleaning up unused Docker images..."
    docker image prune -f | tail -1
    docker builder prune -f --filter "until=24h" >/dev/null 2>&1 || true

    wait_for_health "$(app_url)" || true
    echo
    ok "iSpend updated. All data was preserved."
    echo -e "  URL:       ${GREEN}$(app_url)${NC}"
}

# ---------------------------------------------------------------- ssl only
do_ssl() {
    [ -f "$INSTALL_DIR/docker-compose.yml" ] || fail "No installation found at $INSTALL_DIR — run Install first."
    cd "$INSTALL_DIR"
    install_certbot
    prompt_domain
    prompt_email
    set_env LETSENCRYPT_EMAIL "${LE_EMAIL:-}"
    set_env APP_PORT "$DEFAULT_HTTP_PORT"
    grep -q '^APP_HTTPS_PORT=' "$INSTALL_DIR/.env" || set_env APP_HTTPS_PORT "$DEFAULT_HTTPS_PORT"

    if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
        read -r -p "A certificate for $DOMAIN exists. Force a renewal now? [y/N] " answer
        case "${answer:-N}" in [Yy]*) certbot renew --cert-name "$DOMAIN" --force-renewal || warn "Renewal failed; the existing certificate stays in place." ;; esac
    else
        obtain_certificate "$DOMAIN" "${LE_EMAIL:-}" || fail "Could not obtain a certificate. Fix DNS/firewall and run the SSL option again."
    fi
    enable_ssl "$DOMAIN"
    info "Applying HTTPS configuration..."
    compose up -d --force-recreate
    wait_for_health "$(app_url)" || true
    firewall_hint "$DEFAULT_HTTP_PORT" "$(get_env APP_HTTPS_PORT)"
    ok "HTTPS is active at $(app_url) — renewal is automatic."
}

# ---------------------------------------------------------------- remove
do_remove() {
    [ -d "$INSTALL_DIR" ] || fail "No installation found at $INSTALL_DIR."
    cd "$INSTALL_DIR"
    warn "This stops iSpend and removes its containers."
    read -r -p "Continue? [y/N] " answer
    case "${answer:-N}" in [Yy]*) ;; *) fail "Aborted." ;; esac
    read -r -p "Create a final backup first? [Y/n] " answer
    case "${answer:-Y}" in [Yy]*) backup_data ;; esac
    read -r -p "ALSO DELETE all data (database, uploaded statements)? This cannot be undone. [y/N] " wipe
    local domain; domain="$(get_env DOMAIN)"
    case "${wipe:-N}" in
        [Yy]*) compose down -v --rmi local 2>/dev/null || true; rm -rf "$INSTALL_DIR"; ok "iSpend and ALL data removed. Backups (if any) remain in $BACKUP_DIR" ;;
        *)     compose down --rmi local 2>/dev/null || true; rm -rf "$INSTALL_DIR"; ok "iSpend removed. Data volumes kept — a reinstall will reuse them." ;;
    esac
    rm -f "$RENEW_HOOK"
    if [ -n "$domain" ] && [ -d "/etc/letsencrypt/live/${domain}" ]; then
        read -r -p "Delete the Let's Encrypt certificate for ${domain} too? [y/N] " answer
        case "${answer:-N}" in [Yy]*) certbot delete --cert-name "$domain" --non-interactive || true ;; esac
    fi
}

# ---------------------------------------------------------------- menu
main() {
    require_root
    case "${1:-}" in
        install) do_install; exit 0 ;;
        update)  do_update;  exit 0 ;;
        ssl)     do_ssl;     exit 0 ;;
        remove)  do_remove;  exit 0 ;;
    esac
    echo
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}    iSpend installer (Ubuntu)   ${NC}"
    echo -e "${BLUE}================================${NC}"
    echo "  1) Install (clean, optional HTTPS)"
    echo "  2) Update  (pull latest code, keep all data and certificates)"
    echo "  3) SSL     (install or repair HTTPS for a domain)"
    echo "  4) Remove"
    echo "  5) Exit"
    echo
    read -r -p "Choose an option [1-5]: " choice
    case "$choice" in
        1) do_install ;;
        2) do_update ;;
        3) do_ssl ;;
        4) do_remove ;;
        *) echo "Bye." ;;
    esac
}

main "$@"
