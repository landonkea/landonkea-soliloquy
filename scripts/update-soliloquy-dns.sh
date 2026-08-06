#!/bin/bash
# ───────────────────────────────────────────────────────────────────
# update-soliloquy-dns.sh — keeps soliloquy.internal pointed at this
# Mac's CURRENT LAN IP, since DHCP can (and does) reassign it every
# few hours/days. Run on a timer (see the LaunchDaemon plist next to
# this file) so the dnsmasq record self-heals without anyone noticing
# or having to re-run anything by hand.
#
# Only rewrites dnsmasq.conf and restarts the service when the IP has
# actually changed -- a no-op every other run, so running this every
# few minutes is cheap.
# ───────────────────────────────────────────────────────────────────

set -e

CURRENT_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
if [ -z "$CURRENT_IP" ]; then
    echo "$(date): could not determine a current LAN IP -- not connected to WiFi/Ethernet?" >&2
    exit 1
fi

CONF_FILE="/opt/homebrew/etc/dnsmasq.conf"
NEW_LINE="address=/soliloquy.internal/$CURRENT_IP"
CURRENT_LINE=$(grep "^address=/soliloquy.internal/" "$CONF_FILE" || true)

if [ "$CURRENT_LINE" = "$NEW_LINE" ]; then
    exit 0  # already correct, nothing to do
fi

sed -i '' "s|^address=/soliloquy\.internal/.*|$NEW_LINE|" "$CONF_FILE"
/opt/homebrew/bin/brew services restart dnsmasq > /dev/null
echo "$(date): soliloquy.internal -> $CURRENT_IP (was: ${CURRENT_LINE:-unset})"
