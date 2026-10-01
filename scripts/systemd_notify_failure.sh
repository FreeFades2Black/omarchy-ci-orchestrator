#!/usr/bin/env bash
set -euo pipefail

FAILED_UNIT="${1:-unknown.service}"
UID_NUM="$(id -u)"

# 1. Bind D-Bus session bus
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/${UID_NUM}/bus}"

# 2. Discover active Wayland display socket if available
if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
  WAYLAND_SOCKET=$(find "/run/user/${UID_NUM}" -maxdepth 1 -name "wayland-*" 2>/dev/null | head -n 1 || true)
  if [[ -n "$WAYLAND_SOCKET" ]]; then
    export WAYLAND_DISPLAY="$(basename "$WAYLAND_SOCKET")"
  fi
fi

# 3. Fallback to standard X11 display
export DISPLAY="${DISPLAY:-:0}"

# 4. Dispatch desktop notification with critical urgency
notify-send \
  --urgency=critical \
  --app-name="Systemd Watchdog" \
  --icon="dialog-error" \
  "Service Failure: ${FAILED_UNIT}" \
  "Maintenance job failed. Check logs via: journalctl --user-unit=${FAILED_UNIT} -n 20"
