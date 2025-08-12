#!/usr/bin/with-contenv bashio
# Be cautious with -u in add-ons (unset vars can kill the service before logs flush)
set -o pipefail
export PYTHONUNBUFFERED=1

echo "Starting AppKettle"

# ---- Read config (use has_value to avoid unset/empty pitfalls) ----
mqtt_host="$(bashio::config 'mqtt_host')"
mqtt_port="$(bashio::config 'mqtt_port')"
mqtt_usr="$(bashio::config 'mqtt_usr')"
mqtt_pwd="$(bashio::config 'mqtt_pwd')"
min_lvl="$(bashio::config 'min_lvl')"
max_lvl="$(bashio::config 'max_lvl')"

# Optional fields
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

# ---- Self-contained discovery (no external imports) ----
DISC_FILE="/tmp/appkettle_discovery.log"
: > "$DISC_FILE"

python3 -u - > "$DISC_FILE" 2>&1 <<'PY'
import os, sys, time, json, socket

UDP_PORT = 15103
bcast   = os.environ.get("BCAST", "255.255.255.255")
src_ip  = os.environ.get("SRC_IP") or None

def probe(broadcast_ip, source_ip=None, attempts=5, timeout=5):
    for attempt in range(1, attempts+1):
        try:
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
            send_sock.close()

            print(f"[DISCOVERY] attempt {attempt}/{attempts} bcast={broadcast_ip} src_ip={source_ip}", flush=True)

            rcv_sock.settimeout(timeout)
            try:
                data, address = rcv_sock.recvfrom(1024)
            except socket.timeout:
                rcv_sock.close()
                continue

            data = data.decode("ascii", errors="replace")
            rcv_sock.close()

            parts = data.split("#")
            if len(parts) < 7:
                print(f"[DISCOVERY] WARN unexpected reply: {data}", flush=True)
                continue

            msg_json = json.loads(parts[6])
            imei = parts[0]
            ip = address[0]
            print(f"[DISCOVERY] OK ip={ip} imei={imei}", flush=True)
            # Emit a single machine-parsable result line:
            print(f"AK_RESULT {ip} {imei}", flush=True)
            return True
        except Exception as e:
            print(f"[DISCOVERY] ERROR: {e}", flush=True)
    print("[DISCOVERY] TIMEOUT", flush=True)
    return False

probe(bcast, src_ip)
PY
# export env to the helper
BCAST="$bcast" SRC_IP="$src_ip" python3 -u "$DISC_FILE" >/dev/null 2>&1 || true

# ---- Show discovery logs in the HA log pane ----
echo "Discovery output:"
cat "$DISC_FILE" || true

# ---- Parse result ----
result_line="$(grep '^AK_RESULT ' "$DISC_FILE" | tail -n1 || true)"
kettle_ip="${result_line#AK_RESULT }"
kettle_ip="${kettle_ip%% *}"
kettle_imei="${result_line##* }"

echo "Kettle IP:   ${kettle_ip:-<none>}"
echo "Kettle IMEI: ${kettle_imei:-<none>}"

if [ -z "${kettle_ip:-}" ] || [ -z "${kettle_imei:-}" ]; then
  echo "No kettle detected. Ensure the kettle cannot access the internet and retry."
  # Keep the service from flapping too fast; let the user see logs.
  sleep 10
  exit 1
fi

# ---- Launch main script (unbuffered) ----
cmd=( python3 -u /appkettle_mqtt.py
      "${kettle_ip}" "${kettle_imei}"
      --mqtt "${mqtt_host}" "${mqtt_port}" "${mqtt_usr}" "${mqtt_pwd}"
      --calibrate "${min_lvl}" "${max_lvl}" )

if [ -n "${bcast}" ]; then
  echo "Using broadcast override: ${bcast}"
  cmd+=( --bcast "${bcast}" )
fi

if [ -n "${src_ip}" ]; then
  echo "Using source IP: ${src_ip}"
  cmd+=( --src-ip "${src_ip}" )
fi

echo "Launching: ${cmd[*]}"
exec "${cmd[@]}"
