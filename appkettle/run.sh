#!/usr/bin/with-contenv bashio
# Be cautious with -u in add-ons (unset vars can kill the service before logs flush)
set -o pipefail
export PYTHONUNBUFFERED=1

echo "[RUN] Starting AppKettle add-on..."

# ---- Read config ----
echo "[RUN] Reading config from Home Assistant..."
mqtt_host="$(bashio::config 'mqtt_host')"
mqtt_port="$(bashio::config 'mqtt_port')"
mqtt_usr="$(bashio::config 'mqtt_usr')"
mqtt_pwd="$(bashio::config 'mqtt_pwd')"
min_lvl="$(bashio::config 'min_lvl')"
max_lvl="$(bashio::config 'max_lvl')"

if bashio::config.has_value 'bcast'; then
  bcast="$(bashio::config 'bcast')"
else
  bcast="255.255.255.255"
fi

if bashio::config.has_value 'src_ip'; then
  src_ip="$(bashio::config 'src_ip')"
else
  src_ip=""
fi

echo "[RUN] MQTT Host: $mqtt_host"
echo "[RUN] MQTT Port: $mqtt_port"
echo "[RUN] MQTT User: $mqtt_usr"
echo "[RUN] Min Level: $min_lvl"
echo "[RUN] Max Level: $max_lvl"
echo "[RUN] Broadcast: $bcast"
echo "[RUN] Source IP: $src_ip"

# ---- Discovery ----
DISC_FILE="/tmp/appkettle_discovery.log"
: > "$DISC_FILE"

echo "[RUN] Starting discovery..."
BCAST="$bcast" SRC_IP="$src_ip" python3 -u - > "$DISC_FILE" 2>&1 <<'PY'
import os, sys, time, json, socket

UDP_PORT = 15103
bcast   = os.environ.get("BCAST", "255.255.255.255")
src_ip  = os.environ.get("SRC_IP") or None

print(f"[DISCOVERY] Using broadcast {bcast}, source_ip={src_ip}", flush=True)

def probe(broadcast_ip, source_ip=None, attempts=5, timeout=5):
    for attempt in range(1, attempts+1):
        try:
            print(f"[DISCOVERY] Attempt {attempt}/{attempts}...", flush=True)
            send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            rcv_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            rcv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if source_ip:
                try:
                    send_sock.bind((source_ip, 0))
                    rcv_sock.bind((source_ip, UDP_PORT))
                except OSError as e:
                    print(f"[DISCOVERY] WARN bind({source_ip}) failed: {e}", flush=True)
                    rcv_sock.bind(("", UDP_PORT))
            else:
                rcv_sock.bind(("", UDP_PORT))

            for _ in range(3):
                prb = time.strftime("Probe#%Y-%m-%d-%H-%M-%S", time.localtime())
                send_sock.sendto(prb.encode("ascii"), (broadcast_ip, UDP_PORT))
                print(f"[DISCOVERY] Sent probe: {prb}", flush=True)

            send_sock.close()
            print(f"[DISCOVERY] Waiting {timeout}s for reply...", flush=True)

            rcv_sock.settimeout(timeout)
            try:
                data, address = rcv_sock.recvfrom(1024)
            except socket.timeout:
                print("[DISCOVERY] Timeout waiting for reply", flush=True)
                rcv_sock.close()
                continue

            data = data.decode("ascii", errors="replace")
            rcv_sock.close()

            print(f"[DISCOVERY] Got reply from {address}: {data}", flush=True)
            parts = data.split("#")
            if len(parts) < 7:
                print(f"[DISCOVERY] WARN unexpected reply format", flush=True)
                continue

            msg_json = json.loads(parts[6])
            imei = parts[0]
            ip = address[0]
            print(f"[DISCOVERY] Found kettle IP={ip} IMEI={imei}", flush=True)
            print(f"AK_RESULT {ip} {imei}", flush=True)
            return True
        except Exception as e:
            print(f"[DISCOVERY] ERROR: {e}", flush=True)
    print("[DISCOVERY] Gave up after attempts", flush=True)
    return False

probe(bcast, src_ip)
PY

echo "[RUN] Discovery output from Python:"
cat "$DISC_FILE" || true

# ---- Parse result ----
result_line="$(grep '^AK_RESULT ' "$DISC_FILE" | tail -n1 || true)"
kettle_ip="${result_line#AK_RESULT }"
kettle_ip="${kettle_ip%% *}"
kettle_imei="${result_line##* }"

echo "[RUN] Kettle IP:   ${kettle_ip:-<none>}"
echo "[RUN] Kettle IMEI: ${kettle_imei:-<none>}"

if [ -z "${kettle_ip:-}" ] || [ -z "${kettle_imei:-}" ]; then
  echo "[RUN] No kettle detected. Will retry every 15s..."
  while true; do
    sleep 15
    echo "[RUN] Waiting for kettle... still not found."
  done
fi

# ---- Launch main script ----
cmd=( python3 -u /appkettle_mqtt.py
      "${kettle_ip}" "${kettle_imei}"
      --mqtt "${mqtt_host}" "${mqtt_port}" "${mqtt_usr}" "${mqtt_pwd}"
      --calibrate "${min_lvl}" "${max_lvl}" )

if [ -n "${bcast}" ]; then
  echo "[RUN] Using broadcast override: ${bcast}"
  cmd+=( --bcast "${bcast}" )
fi

if [ -n "${src_ip}" ]; then
  echo "[RUN] Using source IP: ${src_ip}"
  cmd+=( --src-ip "${src_ip}" )
fi

echo "[RUN] Launching main script: ${cmd[*]}"
exec "${cmd[@]}"
