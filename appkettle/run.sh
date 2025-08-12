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

# ---- Self-contained discovery (no external imports) ----
# Never let this kill the shell even if it fails.
set +e
DISCOVERY_OUTPUT="$(
  BCAST="${bcast:-255.255.255.255}" SRC_IP="${src_ip:-}" python3 -u - <<'PY'
import os, sys, time, json, socket

UDP_PORT = 15103

bcast = os.environ.get("BCAST") or "255.255.255.255"
src_ip = os.environ.get("SRC_IP") or None

def probe(broadcast_ip, source_ip=None, attempts=5, timeout=5):
    for _ in range(attempts):
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
                print(f"DISCOVERY WARN: bind to {source_ip} failed: {e}", flush=True)
                rcv_sock.bind(("", UDP_PORT))
        else:
            rcv_sock.bind(("", UDP_PORT))

        for _ in range(3):
            prb = time.strftime("Probe#%Y-%m-%d-%H-%M-%S", time.localtime())
            send_sock.sendto(prb.encode("ascii"), (broadcast_ip, UDP_PORT))

        send_sock.close()
        print(f"Discovery using broadcast {broadcast_ip} and src_ip {source_ip}", flush=True)

        rcv_sock.settimeout(timeout)
        try:
            data, address = rcv_sock.recvfrom(1024)
        except socket.timeout:
            rcv_sock.close()
            continue

        data = data.decode("ascii")
        rcv_sock.close()

        parts = data.split("#")
        if len(parts) < 7:
            print("DISCOVERY WARN: unexpected probe reply:", data, flush=True)
            continue

        msg_json = json.loads(parts[6])
        imei = parts[0]
        ip = address[0]
        print(f"DISCOVERY OK: ip={ip} imei={imei}", flush=True)
        return ip, imei

    return None, None

ip, imei = probe(bcast, src_ip)
if ip and imei:
    print(f"{ip} {imei}", flush=True)
PY
)"
rc=$?
set -e

# Parse "IP IMEI" from last non-empty line
last_line="$(echo "$DISCOVERY_OUTPUT" | awk 'NF{p=$0}END{print p}')"
kettle_ip="${last_line%% *}"
kettle_imei="${last_line#* }"

echo "Discovery output:"
echo "$DISCOVERY_OUTPUT"
echo "Kettle IP:   ${kettle_ip:-<none>}"
echo "Kettle IMEI: ${kettle_imei:-<none>}"

if [ -z "${kettle_ip:-}" ] || [ -z "${kettle_imei:-}" ]; then
  echo "No kettle detected. Ensure the kettle cannot access the internet and retry."
  # Do NOT hard-exit; let s6 keep the service up so you can see logs.
  sleep 10
  exit 1
fi

# ---- Launch main script (always unbuffered) ----
cmd=( python3 -u /appkettle_mqtt.py
      "${kettle_ip}" "${kettle_imei}"
      --mqtt "${mqtt_host}" "${mqtt_port}" "${mqtt_usr}" "${mqtt_pwd}"
      --calibrate "${min_lvl}" "${max_lvl}" )

if [ -n "${bcast:-}" ]; then
  echo "Using broadcast override: ${bcast}"
  cmd+=( --bcast "${bcast}" )
fi

if [ -n "${src_ip:-}" ]; then
  echo "Using source IP: ${src_ip}"
  cmd+=( --src-ip "${src_ip}" )
fi

echo "Launching: ${cmd[*]}"
exec "${cmd[@]}"
