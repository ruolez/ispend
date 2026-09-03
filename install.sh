#!/usr/bin/env bash
#
# iSpend — all-in-one installer for Ubuntu 24 LTS
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/ruolez/ispend/main/install.sh)"
#
# Options: Install (clean) / Update (pull code, keep all data) / Remove
#
set -euo pipefail

REPO_URL="https://github.com/ruolez/ispend.git"
INSTALL_DIR="/opt/ispend"
BACKUP_DIR="/opt/ispend-backups"
DEFAULT_PORT="5559"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || fail "Run this script as root: sudo bash install.sh"
}

compose() {
    docker compose --project-directory "$INSTALL_DIR" "$@"
}

get_env() {
    grep -E "^$1=" "$INSTALL_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true
}

set_env() {
    local key="$1" val="$2" file="$INSTALL_DIR/.env"
    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        echo "${key}=${val}" >> "$file"
    fi
}

# Scale per-container memory limits to the host's RAM (written to .env, read by
# docker-compose.yml). The backend gets the larger share: OCR runs there.
configure_resources() {
    local total reserve budget nginx remaining postgres backend shared eff
    total="$(awk '/^MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
    if [ -z "$total" ] || [ "$total" -le 0 ]; then
        warn "Could not detect host RAM — keeping default resource limits."
        return
    fi
    reserve=$(( total / 5 ))
    [ "$reserve" -lt 768 ] && reserve=768
    budget=$(( total - reserve ))
    [ "$budget" -lt 512 ] && budget=512

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

server_ip() {
    hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost"
}

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

wait_for_health() {
    local port="$1"
    info "Waiting for the application to become healthy..."
    for _ in $(seq 1 60); do
        if curl -fsS "http://localhost:${port}/api/health" >/dev/null 2>&1; then
            ok "Application is up"
            return 0
        fi
        sleep 2
    done
    warn "Health check timed out. Inspect logs with: docker compose --project-directory $INSTALL_DIR logs"
    return 1
}

backup_data() {
    if ! compose ps postgres 2>/dev/null | grep -q "Up"; then
        warn "Postgres container is not running — skipping backup"
        return
    fi
    mkdir -p "$BACKUP_DIR"
    local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
    local dump="$BACKUP_DIR/ispend-db-$stamp.sql.gz"
    info "Backing up database to $dump ..."
    compose exec -T postgres pg_dump -U ispend ispend | gzip > "$dump"
    info "Backing up uploaded statements..."
    docker run --rm -v ispend_statements:/data:ro -v "$BACKUP_DIR":/out alpine \
        tar czf "/out/ispend-statements-$stamp.tgz" -C /data . 2>/dev/null || warn "Statement volume backup skipped"
    cp "$INSTALL_DIR/.env" "$BACKUP_DIR/env-$stamp" 2>/dev/null || true
    ok "Backup complete ($(du -h "$dump" | cut -f1))"
}

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

    local port admin_pass
    read -r -p "Port to expose the app on [${DEFAULT_PORT}]: " port
    port="${port:-$DEFAULT_PORT}"
    admin_pass="$(openssl rand -hex 6)"

    info "Generating .env with random secrets..."
    cat > "$INSTALL_DIR/.env" <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 16)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_INITIAL_PASSWORD=${admin_pass}
APP_PORT=${port}
EOF
    chmod 600 "$INSTALL_DIR/.env"

    info "Tuning container resource limits to this host..."
    configure_resources

    info "Building and starting containers (first build takes a few minutes; it installs the OCR engine)..."
    compose up -d --build

    wait_for_health "$port" || true

    if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
        warn "ufw firewall is active. Allow the app port with: ufw allow ${port}/tcp"
    fi

    local url="http://$(server_ip)"
    [ "$port" != "80" ] && url="${url}:${port}"
    echo
    ok "iSpend installed."
    echo -e "  URL:       ${GREEN}${url}${NC}"
    echo -e "  Username:  ${GREEN}admin${NC}"
    echo -e "  Password:  ${GREEN}${admin_pass}${NC}   (autogenerated — change it in Settings; also saved in ${INSTALL_DIR}/.env)"
    echo -e "  Next:      Settings → Accounts, then Import a statement. AI suggestions are optional (Settings → AI)."
}

do_update() {
    [ -f "$INSTALL_DIR/docker-compose.yml" ] || fail "No installation found at $INSTALL_DIR — run Install first."
    cd "$INSTALL_DIR"

    backup_data

    info "Pulling latest code..."
    git -C "$INSTALL_DIR" fetch origin main
    git -C "$INSTALL_DIR" reset --hard origin/main

    info "Re-tuning container resource limits to this host..."
    configure_resources

    info "Rebuilding and restarting containers (database and statements are kept)..."
    compose up -d --build

    info "Cleaning up unused Docker images..."
    docker image prune -f | tail -1
    docker builder prune -f --filter "until=24h" >/dev/null 2>&1 || true

    local port; port="$(get_env APP_PORT)"
    port="${port:-$DEFAULT_PORT}"
    wait_for_health "$port" || true

    local url="http://$(server_ip)"
    [ "$port" != "80" ] && url="${url}:${port}"
    echo
    ok "iSpend updated. Users, accounts, transactions, rules and statements were preserved."
    echo -e "  URL:       ${GREEN}${url}${NC}"
}

do_remove() {
    [ -d "$INSTALL_DIR" ] || fail "No installation found at $INSTALL_DIR."
    cd "$INSTALL_DIR"

    warn "This stops iSpend and removes its containers."
    read -r -p "Continue? [y/N] " answer
    case "${answer:-N}" in [Yy]*) ;; *) fail "Aborted." ;; esac

    read -r -p "Create a final backup first? [Y/n] " answer
    case "${answer:-Y}" in [Yy]*) backup_data ;; esac

    read -r -p "ALSO DELETE all data (database, uploaded statements)? This cannot be undone. [y/N] " wipe
    case "${wipe:-N}" in
        [Yy]*)
            compose down -v --rmi local 2>/dev/null || true
            rm -rf "$INSTALL_DIR"
            ok "iSpend and ALL data removed. Backups (if any) remain in $BACKUP_DIR"
            ;;
        *)
            compose down --rmi local 2>/dev/null || true
            rm -rf "$INSTALL_DIR"
            ok "iSpend removed. Data volumes kept — a reinstall will reuse them."
            ;;
    esac
}

main() {
    require_root
    case "${1:-}" in
        install) do_install; exit 0 ;;
        update)  do_update;  exit 0 ;;
        remove)  do_remove;  exit 0 ;;
    esac

    echo
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}    iSpend installer (Ubuntu)   ${NC}"
    echo -e "${BLUE}================================${NC}"
    echo "  1) Install (clean)"
    echo "  2) Update  (pull latest code, keep all data)"
    echo "  3) Remove"
    echo "  4) Exit"
    echo
    read -r -p "Choose an option [1-4]: " choice
    case "$choice" in
        1) do_install ;;
        2) do_update ;;
        3) do_remove ;;
        *) echo "Bye." ;;
    esac
}

main "$@"
