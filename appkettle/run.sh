#!/usr/bin/with-contenv bashio
set -euo pipefail

echo "Starting AppKettle"

mqtt_host="$(bashio::config 'mqtt_host')"
mqtt_port="$(bashio::config 'mqtt_port')"
mqtt_usr="$(bashio::config 'mqtt_usr')"
mqtt_pwd="$(bashio::config 'mqtt_pwd')"
min_lvl="$(bashio::config 'min_lvl')"
max_lvl="$(bashio::config 'max_lvl')"
bcast="$(bashio::config 'bcast' || true)"   # optional

# Discover kettle IP + IMEI exactly once via Python (unbuffered)
# Uses the broadcast override if provided
DISCOVERY_OUTPUT="$(
  BCAST="${bcast:-}" python3 -u - <<'PY'
import os, sys, time
# Import your scripts classes without running argparser
import appkettle_mqtt as mod

bcast = os.environ.get("BCAST") or getattr(mod, "UDP_IP_BCAST_DEFAULT", "255.255.255.255")
ks = mod.KettleSocket(imei="", broadcast_ip=bcast)

print(f"Discovery using broadcast {bcast}", flush=True)
info = ks.kettle_probe()  # prints progress itself

if not info:
    print("", flush=True)  # empty result -> handled by shell
    sys.exit(0)

# Print space-separated so shell can parse without jq
print(f"{info.get('kettleIP','')} {info.get('imei','')}", flush=True)
PY
)"

# Parse "IP IMEI" from the last line of discovery output
# (If discovery printed multiple lines, grab the last non-empty line)
last_line="$(echo "$DISCOVERY_OUTPUT" | awk 'NF{p=$0}END{print p}')"
kettle_ip="${last_line%% *}"
kettle_imei="${last_line#* }"

echo "Discovery output:"
echo "$DISCOVERY_OUTPUT"
echo "Kettle IP:   ${kettle_ip:-<none>}"
echo "Kettle IMEI: ${kettle_imei:-<none>}"

if [ -z "${kettle_ip:-}" ] || [ -z "${kettle_imei:-}" ]; then
  echo "No kettle detected. Ensure the kettle cannot access the internet and retry."
  exit 1
fi

# Build command (always unbuffered)
cmd=( python3 -u /appkettle_mqtt.py
      "${kettle_ip}" "${kettle_imei}"
      --mqtt "${mqtt_host}" "${mqtt_port}" "${mqtt_usr}" "${mqtt_pwd}"
      --calibrate "${min_lvl}" "${max_lvl}" )

# Only add --bcast if set
if [ -n "${bcast:-}" ]; then
  echo "Using broadcast override: ${bcast}"
  cmd+=( --bcast "${bcast}" )
fi

echo "Launching: ${cmd[*]}"
exec "${cmd[@]}"
