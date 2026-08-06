#!/bin/bash
# ───────────────────────────────────────────────────────────────────
# soliloquy-mdns.sh — publishes "soliloquy.local" via real mDNS
# ───────────────────────────────────────────────────────────────────
# Registers a DEDICATED mDNS hostname ("soliloquy.local") pointed at
# this Mac's current LAN IP, separate from -- and without touching --
# the Mac's own Bonjour name (its regular .local hostname stays
# whatever it already was). Real mDNS means every device on the LAN
# resolves it automatically (iOS, Android, macOS, Linux/avahi, modern
# Windows) with ZERO per-device configuration -- no DNS settings to
# change anywhere, unlike the dnsmasq/.internal approach this
# replaces for cross-device use.
#
# `dns-sd -P` registers and holds the name for as long as the process
# runs, so this script keeps it alive as a persistent loop (meant to
# run under a LaunchAgent with KeepAlive), and re-registers whenever
# the LAN IP changes (DHCP reassigns it every few hours/days).
# ───────────────────────────────────────────────────────────────────

DNS_SD_PID=""
LAST_IP=""

cleanup() {
    [ -n "$DNS_SD_PID" ] && kill "$DNS_SD_PID" 2>/dev/null
    exit 0
}
trap cleanup TERM INT

while true; do
    CURRENT_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
    if [ -n "$CURRENT_IP" ] && [ "$CURRENT_IP" != "$LAST_IP" ]; then
        if [ -n "$DNS_SD_PID" ]; then
            kill "$DNS_SD_PID" 2>/dev/null
            wait "$DNS_SD_PID" 2>/dev/null
        fi
        dns-sd -P "Soliloquy" _http._tcp . 8000 soliloquy.local "$CURRENT_IP" &
        DNS_SD_PID=$!
        LAST_IP="$CURRENT_IP"
        echo "$(date): soliloquy.local -> $CURRENT_IP"
    fi
    sleep 60
done
