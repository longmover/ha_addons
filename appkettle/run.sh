#!/usr/bin/with-contenv bashio
set -euo pipefail

export PYTHONUNBUFFERED=1

echo "Starting AppKettle"

mqtt_host="$(bashio::config 'mqtt_host')"
mqtt_port="$(bashio::config 'mqtt_port')"
mqtt_usr="$(bashio::config 'mqtt_usr')"
mqtt_pwd="$(bashio::config 'mqtt_pwd')"
min_lvl="$(bashio::config 'min_lvl')"
max_lvl="$(bashio::config 'max_lvl')"
bcast="$(bashio::config 'bcast' || true)"      # optional
src_ip="$(bashio::config 'src_ip' || true)"    # optional

# Discover kettle IP + IMEI exactly once via Python (unbuffered).
# Uses the broadcast override and/or source IP if provided.
DISCOVERY_OUTPUT="$(
  BCAST="${bcast:-}" SRC_IP="${src_ip:-}" python3 -u - <<'PY'
import os, sys, time
# Import your script's classes without running argparser
import appkettle_mqtt as mod

bcast = os.environ.get("BCAST") or getattr(mod, "UDP_IP_BCAST_DEFAULT", "255.255.255.255")
src_ip = os.environ.get("SRC_IP") or None
ks = mod.KettleSocket(imei="", broadcast_ip=bcast, source_ip=src_ip)

print(f"Discovery using broadcast {bcast} and src_ip {src_ip}", flush=True)
info = ks.kettle_probe()  # prints progress itself

if not info:
    print("", flush=True)  # empty result -> handled by shell
    sys.exit(0)

# Print space-separated so shell can parse without jq
print(f"{info.get('kettleIP','')} {info.get('imei','')}", flush=True)
PY
)"

# Parse "IP IMEI" from the last non-empty line
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

# Only add --src-ip if set
if [ -n "${src_ip:-}" ]; then
  echo "Using source IP: ${src_ip}"
  cmd+=( --src-ip "${src_ip}" )
fi

echo "Launching: ${cmd[*]}"
exec "${cmd[@]}"
