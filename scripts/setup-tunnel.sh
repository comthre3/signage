#!/usr/bin/env bash
#
# Install Cloudflare Tunnel (cloudflared) as a system service.
#
# The tunnel is what makes this stack reachable on your public hostnames
# without opening any inbound ports. It runs on the host, OUTSIDE the Docker
# stack, which is why moving the service to a new machine needs this step even
# though redeploy.sh handles everything else.
#
#   scripts/setup-tunnel.sh            # install and enable
#   scripts/setup-tunnel.sh --status   # report what is installed, change nothing
#   scripts/setup-tunnel.sh --uninstall
#
# You supply a tunnel token from your own Cloudflare Zero Trust dashboard. The
# token is read directly from your terminal, never echoed, never written to the
# repo, and never passed on a command line other people could see in `ps`.
set -euo pipefail

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
ok()   { printf "  ${GRN}ok${RST}    %s\n" "$*"; }
warn() { printf "  ${YEL}warn${RST}  %s\n" "$*"; }
err()  { printf "  ${RED}FAIL${RST}  %s\n" "$*"; }
step() { printf "\n${DIM}==>${RST} %s\n" "$*"; }
die()  { printf "\n${RED}aborted:${RST} %s\n" "$*" >&2; exit 1; }

MODE=install
for a in "$@"; do
  case "$a" in
    --status)    MODE=status ;;
    --uninstall) MODE=uninstall ;;
    --help|-h)   sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "need root or sudo to manage a system service"
  SUDO="sudo"
fi

installed() { command -v cloudflared >/dev/null 2>&1; }
svc_state() { systemctl is-active cloudflared 2>/dev/null || echo "not-installed"; }

report() {
  step "Cloudflare Tunnel status"
  if installed; then
    ok "cloudflared $(cloudflared --version 2>/dev/null | awk '{print $3}')"
  else
    warn "cloudflared is not installed"
    return
  fi
  case "$(svc_state)" in
    active)   ok "service running" ;;
    inactive) warn "service installed but stopped ($SUDO systemctl start cloudflared)" ;;
    failed)   err "service failed ($SUDO journalctl -u cloudflared -n 50)" ;;
    *)        warn "no cloudflared service registered" ;;
  esac
}

if [ "$MODE" = status ]; then report; exit 0; fi

if [ "$MODE" = uninstall ]; then
  step "Removing the tunnel service"
  installed || die "cloudflared is not installed"
  $SUDO cloudflared service uninstall 2>/dev/null || warn "service was not registered"
  ok "service removed (the cloudflared binary is left in place)"
  exit 0
fi

# ── install ───────────────────────────────────────────────────────────
step "Checking the host"

[ -d /run/systemd/system ] || die "systemd not detected; cloudflared's service installer needs it.
        Run cloudflared manually, or install it on the host rather than inside a container."
ok "systemd present"

if installed && [ "$(svc_state)" = active ]; then
  ok "cloudflared already installed and running"
  report
  printf "\n${DIM}Nothing to do. Use --uninstall to remove it first if you want to re-run setup.${RST}\n"
  exit 0
fi

if ! installed; then
  . /etc/os-release 2>/dev/null || die "cannot identify this OS"
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$ID" in
    debian|ubuntu) ;;
    *) die "automatic install covers Debian/Ubuntu only (found: $ID).
        Install cloudflared yourself, then re-run this script." ;;
  esac

  step "Installing cloudflared from Cloudflare's package repository"
  # The signed apt repo is used rather than piping a downloaded script to a
  # shell: the package is GPG-verified on install and on every later upgrade.
  $SUDO mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
    | $SUDO tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null \
    || die "could not fetch Cloudflare's signing key"
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $VERSION_CODENAME main" \
    | $SUDO tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  $SUDO apt-get update -qq || die "apt-get update failed"
  $SUDO apt-get install -y cloudflared || die "cloudflared install failed"
  ok "cloudflared $(cloudflared --version 2>/dev/null | awk '{print $3}') installed ($arch)"
else
  ok "cloudflared already installed"
fi

# ── token ─────────────────────────────────────────────────────────────
step "Connecting the tunnel"
cat <<'EOF'
  In the Cloudflare dashboard:
    Zero Trust -> Networks -> Tunnels -> Create a tunnel -> Cloudflared
  Name it, then copy the token out of the install command it shows you
  (the long string after "service install").

  Paste it below. It is not echoed and is handed to cloudflared on stdin,
  so it will not appear in your shell history or in `ps`.

EOF
printf "  Tunnel token (blank to skip): "
read -rs TOKEN </dev/tty || TOKEN=""
printf "\n"

if [ -z "$TOKEN" ]; then
  warn "skipped — no token entered"
  printf "\n${DIM}Run this again when you have one, or install manually with:\n"
  printf "  sudo cloudflared service install <token>${RST}\n\n"
  exit 0
fi

case "$TOKEN" in
  *[!A-Za-z0-9._-]*) die "that does not look like a tunnel token (unexpected characters)" ;;
esac
[ "${#TOKEN}" -ge 40 ] || die "that token looks too short to be valid (${#TOKEN} chars)"

step "Registering the service"
if $SUDO cloudflared service install "$TOKEN"; then
  ok "service installed"
else
  die "cloudflared refused the token — check it was copied whole"
fi
unset TOKEN

$SUDO systemctl enable --now cloudflared >/dev/null 2>&1 || true
sleep 3
case "$(svc_state)" in
  active) ok "tunnel is running" ;;
  *) err "service is not active — inspect with: $SUDO journalctl -u cloudflared -n 50"
     exit 1 ;;
esac

# ── what still has to be done in the dashboard ────────────────────────
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
envget() { grep -E "^$1=" "$ROOT_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- ; }
host_of() { printf '%s' "${1:-}" | sed -E 's#^https?://##; s#/.*$##'; }

printf "\n${GRN}Tunnel connected.${RST} One step left, in the dashboard:\n\n"
printf "  Zero Trust -> Networks -> Tunnels -> your tunnel -> Public Hostname\n"
printf "  Add a route for each hostname, pointing at these local services:\n\n"
printf "    %-32s ->  http://localhost:3000\n" "$(host_of "$(envget APP_URL)")"
printf "    %-32s ->  http://localhost:3001\n" "$(host_of "$(envget PLAYER_BASE_URL)")"
printf "    %-32s ->  http://localhost:8000\n" "$(host_of "$(envget API_BASE_URL)")"
printf "    %-32s ->  http://localhost:3003\n" "$(host_of "$(envget LANDING_URL)")"
printf "\n  ${DIM}status: scripts/setup-tunnel.sh --status"
printf "\n  logs:   $SUDO journalctl -u cloudflared -f${RST}\n\n"
