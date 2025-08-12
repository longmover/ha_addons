#!/usr/bin/with-contenv bashio
# Keep logs visible and avoid early exits
set -o pipefail
export PYTHONUNBUFFERED=1

echo "[RUN] Starting AppKettle add-on..."

# ---- Read config ----
mqtt_host="$(bashio::config 'mqtt_host')"
mqtt_port="$(bashio::config 'mqtt_port')"
mqtt_usr="$(bashio::config 'mqtt_usr')"
mqtt_pwd="$(bashio::config 'mqtt_pwd')"
min_lvl="$(bashio::config 'min_lvl')"
max_lvl="$(bashio::config 'max_lvl')"
kettle_ip="$(bashio::config 'kettle_ip')"

echo "[RUN] MQTT Host: ${mqtt_host}"
echo "[RUN] MQTT Port: ${mqtt_port}"
echo "[RUN] MQTT User: ${mqtt_usr}"
echo "[RUN] Min Level: ${min_lvl}"
echo "[RUN] Max Level: ${max_lvl}"
echo "[RUN] Kettle IP (optional): ${kettle_ip}"

# ---- Build launch command ----
cmd=( python3 -u /appkettle_mqtt.py )

# If user provided kettle_ip, pass it as the positional 'host' and let the script
# unicast-probe the IMEI automatically. Otherwise, no host/imei -> script will try broadcast.
if [ -n "${kettle_ip}" ]; then
  echo "[RUN] Using known kettle IP: ${kettle_ip}"
  cmd+=( "${kettle_ip}" )  # IMEI omitted on purpose; script will derive it
else
  echo "[RUN] No kettle IP configured. Script will attempt broadcast discovery."
fi

# MQTT + calibration args
cmd+=( --mqtt "${mqtt_host}" "${mqtt_port}" "${mqtt_usr}" "${mqtt_pwd}" \
      --calibrate "${min_lvl}" "${max_lvl}" )

echo "[RUN] Launching main script: ${cmd[*]}"
exec "${cmd[@]}"
