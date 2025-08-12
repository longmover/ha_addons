#! /usr/bin/python3
"""Provides a running daemon for interfacing with an appKettle

usage: appkettle_mqtt.py [-h]
                         [--mqtt host port username password]
                         [--calibrate lvl_min lvl_max]
                         [--port PORT]
                         [host] [imei]

arguments:
  host              kettle host or IP
  imei              kettle IMEI (e.g. GD0-12300-35aa)

optional arguments:
  -h, --help        show this help message and exit
  --mqtt host port username password
                    MQTT broker host, port, username & password (e.g. --mqtt 192.168.0.1 1883 mqtt_user p@55w0Rd)
  --calibrate lvl_min lvl_max
                    Min and max volume values for the kettle water level sensor (e.g. --calibrate 160 1640)
  --port PORT       kettle port (default 6002)

Notes:
- If you supply a host IP but omit IMEI, the script will unicast-probe that IP to fetch the IMEI.
- If you supply neither host nor IMEI, the script will attempt broadcast discovery.
- Be sure to block the kettle’s internet access to force local mode.
"""

import sys
import time
import socket
import select
import signal
import json
import argparse
from functools import partial

# Flush prints so logs show up immediately in HA
print = partial(print, flush=True)

import paho.mqtt.client as mqtt     # pip install paho-mqtt
from Cryptodome.Cipher import AES   # pip install pycryptodomex

from protocol_parser import unpack_msg, calc_msg_checksum

DEBUG_MSG = True
DEBUG_PRINT_STAT_MSG = False
DEBUG_PRINT_KEEP_CONNECT = False
SEND_ENCRYPTED = False
MSGLEN = 3200

KETTLE_SOCKET_CONNECT_ATTEMPTS = 3
KETTLE_SOCKET_TIMEOUT_SECS = 60
KEEP_WARM_MINS = 10

ENCRYPT_HEADER = bytes([0x23, 0x23, 0x38, 0x30])
PLAIN_HEADER   = bytes([0x23, 0x23, 0x30, 0x30])
MSG_KEEP_CONNECT = b"##000bKeepConnect&&"
MSG_KEEP_CONNECT_FREQ_SECS = 30
UDP_IP_BCAST_DEFAULT = "255.255.255.255"
UDP_PORT = 15103

MQTT_BASE = "appKettle/"
MQTT_COMMAND_TOPIC = MQTT_BASE + "command"
MQTT_STATUS_TOPIC = MQTT_BASE + "status"
MQTT_AVAILABILITY_TOPIC = MQTT_BASE + "state"
MQTT_DEVICE_ID = "appKettle"
MQTT_SWITCH_DISC_TOPIC = "homeassistant/switch/" + MQTT_DEVICE_ID
MQTT_SENSOR_DISC_TOPIC = "homeassistant/sensor/" + MQTT_DEVICE_ID
MQTT_NUMBER_DISC_TOPIC = "homeassistant/number/" + MQTT_DEVICE_ID
MQTT_DEVICE_NAME = "appKettle"
MQTT_DEVICE_MANUFACTURER = "appKettle"
MQTT_DEVICE_MODEL = "appKettle"

# AES secrets:
SECRET_KEY = b"ay3$&dw*ndAD!9)<"
SECRET_IV  = b"7e3*WwI(@Dczxcue"

class AppKettle:
    """Represents a physical appKettle"""

    def __init__(self, sock=None):
        self.sock = sock
        self.stat = {
            "cmd": "unk",
            "status": "unk",
            "keep_warm_secs": 0,
            "keep_warm_onoff": False,
            "temperature": 0,
            "target_temp": 0,
            "set_target_temp": 100,
            "volume": 0,
            "power": "OFF",
            "seq": 0,
        }

    def tick(self):
        self.stat["seq"] = (self.stat["seq"] + 1) % 0xFF

    def turn_on(self, temp=None):
        if self.stat["status"] != "Ready":
            self.wake()
        self.tick()
        if temp is None:
            temp = self.stat["set_target_temp"]
        msg = "AA001200000000000003B7{seq}39000000{temp}{kw}0000".format(
            temp=("%0.2X" % temp),
            kw=("%0.2X" % (KEEP_WARM_MINS * self.stat["keep_warm_onoff"])),
            seq=("%0.2x" % self.stat["seq"]),
        )
        msg = calc_msg_checksum(msg, append=True)
        return self.sock.send_enc(msg, SEND_ENCRYPTED)

    def wake(self):
        self.tick()
        msg = "AA000D00000000000003B7{seq}410000".format(seq=("%0.2x" % self.stat["seq"]))
        msg = calc_msg_checksum(msg, append=True)
        return self.sock.send_enc(msg, SEND_ENCRYPTED)

    def turn_off(self):
        self.tick()
        msg = "AA000D00000000000003B7{seq}3A0000".format(seq=("%0.2x" % self.stat["seq"]))
        msg = calc_msg_checksum(msg, append=True)
        return self.sock.send_enc(msg)

    def status_json(self):
        keys = {"power", "status", "temperature", "target_temp", "volume", "keep_warm_secs"}
        status_dict = {k: self.stat[k] for k in self.stat.keys() & keys}
        return json.dumps(status_dict)

    def update_status(self, msg):
        try:
            cmd_dict = unpack_msg(msg, DEBUG_MSG, DEBUG_PRINT_STAT_MSG, DEBUG_PRINT_KEEP_CONNECT)
        except ValueError:
            print("Error in decoding: ", msg)
            return
        if not isinstance(cmd_dict, dict):
            return
        if "data3" in cmd_dict:
            try:
                self.stat.update(cmd_dict)
            except ValueError:
                print("Error in data3 cmd_dict: ", cmd_dict)
        elif "data2" in cmd_dict:
            pass
        else:
            print("Unparsed Json message: ", cmd_dict)

class KettleSocket:
    """Handles connection, encryption and decryption for an AppKettle"""

    def __init__(self, sock=None, imei="", broadcast_ip=UDP_IP_BCAST_DEFAULT):
        if sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(KETTLE_SOCKET_TIMEOUT_SECS)
        else:
            self.sock = sock
        self.connected = False
        self.imei = imei
        self.stat = ""
        self.broadcast_ip = broadcast_ip

    def connect(self, host_port):
        attempts = KETTLE_SOCKET_CONNECT_ATTEMPTS
        print("Attempting to connect to socket...")
        while attempts and not self.connected:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(KETTLE_SOCKET_TIMEOUT_SECS)
                self.sock.connect(host_port)
                self.keep_connect()
                self.connected = True
                return
            except (TimeoutError, OSError) as err:
                print("Socket error:", err, "|", attempts, "attempts remaining")
                attempts -= 1
                self.connected = False
        print("Socket timeout")
        self.connected = False

    # ---------- Discovery helpers ----------
    def _parse_probe_reply(self, payload, address):
        parts = payload.split("#")
        if len(parts) < 7:
            print("[DISCOVERY] Unexpected reply format")
            return None
        msg_json = json.loads(parts[6])
        msg_json.update({"imei": parts[0], "version": parts[3], "kettleIP": address[0]})
        return msg_json

    def kettle_probe_unicast(self, ip, timeout=5):
        """Send a unicast probe to a known IP and parse reply to get IMEI & info."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", UDP_PORT))
        except OSError as e:
            print(f"[DISCOVERY] bind(UDP {UDP_PORT}) failed:", e)
            sock.close()
            return None

        # Required "-2" suffix in probe
        prb = time.strftime("Probe#%Y-%m-%d-%H-%M-%S-2", time.localtime())
        print(f"[DISCOVERY] Sending unicast probe to {ip}: {prb}")
        sock.sendto(prb.encode("ascii"), (ip, UDP_PORT))

        sock.settimeout(timeout)
        try:
            data, address = sock.recvfrom(1024)
        except socket.timeout:
            print(f"[DISCOVERY] No reply from {ip} within {timeout}s")
            sock.close()
            return None
        payload = data.decode("ascii", errors="replace")
        print(f"[DISCOVERY] Got reply from {address}: {payload}")
        sock.close()

        info = self._parse_probe_reply(payload, address)
        if info:
            print(f"[DISCOVERY] Unicast success: IP={info.get('kettleIP')} IMEI={info.get('imei')}")
            self.stat = info
        return info

    def kettle_probe(self, attempts=5, timeout=5):
        """Broadcast probe (L2) to discover kettle and return info dict."""
        for attempt in range(1, attempts + 1):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("", UDP_PORT))  # single socket for send/recv
                except OSError as e:
                    print(f"[DISCOVERY] bind(UDP {UDP_PORT}) failed:", e)
                    sock.close()
                    break

                print(f"[DISCOVERY] Broadcast attempt {attempt}/{attempts} bcast={self.broadcast_ip}")
                # Send several probes with "-2" suffix
                for _ in range(4):
                    prb = time.strftime("Probe#%Y-%m-%d-%H-%M-%S-2", time.localtime())
                    sock.sendto(prb.encode("ascii"), (self.broadcast_ip, UDP_PORT))
                    print(f"[DISCOVERY] Sent probe: {prb}")

                sock.settimeout(timeout)
                try:
                    data, address = sock.recvfrom(1024)
                except socket.timeout:
                    print(f"[DISCOVERY] Timeout after {timeout}s waiting for reply")
                    sock.close()
                    continue

                payload = data.decode("ascii", errors="replace")
                print(f"[DISCOVERY] Got reply from {address}: {payload}")
                sock.close()

                info = self._parse_probe_reply(payload, address)
                if info:
                    print("[DISCOVERY] Broadcast success")
                    self.stat = info
                    return info
            except Exception as e:
                print(f"[DISCOVERY] Broadcast error: {e}")

        print(f"[DISCOVERY] No response after {attempts} attempts")
        return None
    # ---------- end discovery helpers ----------

    def keep_connect(self):
        if DEBUG_PRINT_KEEP_CONNECT:
            print("A: KeepConnect")
        try:
            self.sock.sendall(MSG_KEEP_CONNECT)
        except OSError as err:
            print("Socket error (keep connect):", err)
            self.connected = False

    def close(self):
        print("Closing socket...")
        self.sock.close()

    def send(self, msg):
        try:
            sent = self.sock.sendall(msg)
        except OSError as err:
            print("Socket error (send):", err)
            self.connected = False
            return
        if sent is not None:
            self.connected = False
            raise RuntimeError("Socket connection broken")

    def receive(self):
        chunks = []
        bytes_recd = 0
        while bytes_recd < MSGLEN and chunks[-2:] != [b"&", b"&"] and self.connected:
            try:
                chunk = self.sock.recv(1)
                chunks.append(chunk)
                bytes_recd += len(chunk)
            except socket.error:
                print("Socket connection broken?")
                self.connected = False
                return None
            if chunk == b"":
                print("Socket connection broken / no data")
                self.connected = False
                return None

        frame = b"".join(chunks).partition(b"##")
        frame = frame[1] + frame[2]

        if frame[:4] == ENCRYPT_HEADER:
            res = self.decrypt(frame[6:-2])
        elif frame[:4] == PLAIN_HEADER:
            res = frame[6:-2]
        else:
            res = frame
            if len(frame) > 0:
                print("Response not recognised", frame)

        try:
            res = res.decode("ascii")
            return to_json(res.rstrip("\x00"))
        except UnicodeDecodeError:
            return None

    @staticmethod
    def decrypt(ciphertext):
        try:
            cipher_spec = AES.new(SECRET_KEY, AES.MODE_CBC, SECRET_IV)
            return cipher_spec.decrypt(ciphertext)
        except ValueError:
            print("Not 16-byte boundary data")
            return ciphertext
        except Exception:
            print("Unexpected error:", sys.exc_info()[0])
            raise

    @staticmethod
    def pad(data_to_pad, block):
        extra = len(data_to_pad) % block
        if extra > 0:
            return data_to_pad + (b"\x00" * (block - extra))
        return data_to_pad

    def encrypt(self, plaintext):
        try:
            cipher_spec = AES.new(SECRET_KEY, AES.MODE_CBC, SECRET_IV)
            return cipher_spec.encrypt(self.pad(plaintext, AES.block_size))
        except ValueError:
            print("Not 16-byte boundary data:", plaintext)
            return plaintext
        except Exception:
            print("Unexpected error:", sys.exc_info()[0])
            raise

    def send_enc(self, data2, encrypt=False):
        msg = '{{"app_cmd":"62","imei":"{imei}","SubDev":"","data2":"{data2}"}}'.format(
            imei=self.imei, data2=data2
        )
        if encrypt:
            content = self.encrypt(msg.encode())
            header = ENCRYPT_HEADER
        else:
            content = msg.encode()
            header = PLAIN_HEADER
        encoded_msg = header + bytes("%0.2X" % len(content), "utf-8") + content + b"&&"
        self.send(encoded_msg)
        if DEBUG_MSG:
            unpack_msg(to_json(msg))

def cb_mqtt_on_connect(client, kettle, flags, rec_code):
    print("Connected to MQTT broker with result code " + str(rec_code))
    client.subscribe(MQTT_COMMAND_TOPIC + "/#")

def cb_mqtt_on_message(mqttc, kettle, msg):
    print("MQTT MSG: " + msg.topic + " : " + str(msg.payload))
    kettle.wake()
    if msg.topic == MQTT_COMMAND_TOPIC + "/power":
        if msg.payload == b"ON":
            kettle.turn_on()
        elif msg.payload == b"OFF":
            kettle.turn_off()
        else:
            print("MQTT MSG: msg not recognised:", msg)
        mqttc.publish(MQTT_STATUS_TOPIC + "/power", kettle.stat["power"])
    elif msg.topic == MQTT_COMMAND_TOPIC + "/keep_warm_onoff":
        if msg.payload == b"True":
            kettle.stat["keep_warm_onoff"] = True
        elif msg.payload == b"False":
            kettle.stat["keep_warm_onoff"] = False
        else:
            print("MQTT MSG: msg not recognised:", msg)
        mqttc.publish(MQTT_STATUS_TOPIC + "/keep_warm_onoff", kettle.stat["keep_warm_onoff"])
    elif msg.topic == MQTT_COMMAND_TOPIC + "/set_target_temp":
        kettle.stat["set_target_temp"] = int(msg.payload)
        mqttc.publish(MQTT_STATUS_TOPIC + "/set_target_temp", kettle.stat["set_target_temp"])

def to_json(myjson):
    try:
        json_object = json.loads(myjson)
    except (ValueError, TypeError):
        return myjson
    return json_object

def main_loop(host_port, imei, mqtt_broker, lvl_calib=None):
    """Main event loop called from __main__"""
    # calibration defaults
    if not lvl_calib:
        lvl_calib = [160, 1640]
    else:
        lvl_calib = [int(lvl_calib[0]), int(lvl_calib[1])]
    print("Calibration:", lvl_calib)

    kettle_socket = KettleSocket(imei=imei or "")
    kettle = AppKettle(kettle_socket)

    # Discovery rules:
    # - If host provided but imei missing: unicast probe that host to fetch IMEI/info
    # - If neither provided: broadcast discovery
    if host_port[0] and not imei:
        print("[DISCOVERY] Known host provided, discovering IMEI via unicast…")
        info = kettle_socket.kettle_probe_unicast(host_port[0])
        if not info:
            print("Discovery (unicast) failed. Exiting.")
            sys.exit(1)
        imei = info["imei"]
        kettle_socket.imei = imei
        kettle.stat.update(info)
    elif not host_port[0]:
        print("[DISCOVERY] No host provided, attempting broadcast discovery…")
        info = kettle_socket.kettle_probe()
        if not info:
            print("Discovery (broadcast) failed and no host provided. Exiting.")
            sys.exit(1)
        host_port = (info["kettleIP"], host_port[1])
        imei = info["imei"]
        kettle_socket.imei = imei
        kettle.stat.update(info)
    else:
        print("[DISCOVERY] Host and IMEI provided; skipping discovery.")

    mqttc = None
    if mqtt_broker is not None:
        mqttc = mqtt.Client()
        if mqtt_broker[2] is not None:
            mqttc.username_pw_set(mqtt_broker[2], password=mqtt_broker[3])
        mqttc.on_connect = cb_mqtt_on_connect
        mqttc.on_message = cb_mqtt_on_message
        mqttc.user_data_set(kettle)
        mqttc.will_set(MQTT_AVAILABILITY_TOPIC, "offline", retain=True)
        mqttc.connect(mqtt_broker[0], int(mqtt_broker[1]))
        mqttc.publish(MQTT_AVAILABILITY_TOPIC, "online", retain=True)

        # Home Assistant discovery entities
        mqttc.publish(
            MQTT_SWITCH_DISC_TOPIC + "/power/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "command_topic": MQTT_COMMAND_TOPIC + "/power",
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Power",
                "state_topic": MQTT_STATUS_TOPIC + "/power",
                "unique_id": "kettle_power",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:kettle"
            }),
            retain=True
        )
        mqttc.publish(
            MQTT_SWITCH_DISC_TOPIC + "/keep_warm_onoff/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Keep Warm",
                "state_topic": MQTT_STATUS_TOPIC + "/keep_warm_onoff",
                "command_topic": MQTT_COMMAND_TOPIC + "/keep_warm_onoff",
                "unique_id": "kettle_keep_warm",
                "payload_on": "True",
                "payload_off": "False",
                "icon": "mdi:kettle-steam"
            }),
            retain=True
        )
        mqttc.publish(
            MQTT_NUMBER_DISC_TOPIC + "/set_target_temp/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Target Temperature",
                "state_topic": MQTT_STATUS_TOPIC + "/set_target_temp",
                "command_topic": MQTT_COMMAND_TOPIC + "/set_target_temp",
                "unique_id": "kettle_target_temp",
                "unit_of_measurement": "C",
                "icon": "mdi:thermometer-check",
                "max": 100,
                "min": 30
            }),
            retain=True
        )
        mqttc.publish(
            MQTT_SENSOR_DISC_TOPIC + "/current_temp/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Current Temperature",
                "state_topic": MQTT_STATUS_TOPIC + "/temperature",
                "unique_id": "kettle_temp",
                "unit_of_measurement": "C",
                "icon": "mdi:water-thermometer"
            }),
            retain=True
        )
        mqttc.publish(
            MQTT_SENSOR_DISC_TOPIC + "/fill_level/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Fill Level",
                "state_topic": MQTT_STATUS_TOPIC + "/STATE",
                "unique_id": "kettle_fill_level",
                "unit_of_measurement": "%",
                "icon": "mdi:cup-water",
                "value_template": "{{ (min(100, max(0, (((value_json.volume - " + str(lvl_calib[0]) + ") / (" + str(lvl_calib[1]) + " - " + str(lvl_calib[0]) + ")) * 100)|round(0)))) }}"
            }),
            retain=True
        )
        mqttc.publish(
            MQTT_SENSOR_DISC_TOPIC + "/raw_fill_level/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Water Volume",
                "state_topic": MQTT_STATUS_TOPIC + "/volume",
                "unique_id": "kettle_water_volume",
                "icon": "mdi:cup-water"
            }),
            retain=True
        )
        mqttc.publish(
            MQTT_SENSOR_DISC_TOPIC + "/status/config",
            json.dumps({
                "availability": [{"topic": MQTT_AVAILABILITY_TOPIC}],
                "device": {
                    "identifiers": MQTT_DEVICE_NAME,
                    "manufacturer": MQTT_DEVICE_MANUFACTURER,
                    "model": MQTT_DEVICE_MODEL,
                    "name": MQTT_DEVICE_NAME
                },
                "name": "Kettle Status",
                "state_topic": MQTT_STATUS_TOPIC + "/status",
                "unique_id": "kettle_status",
                "icon": "mdi:kettle-alert"
            }),
            retain=True
        )

        mqttc.loop_start()

    def cb_signal_handler(sig, frame):
        user_input = input("prompt|>> ")
        if user_input == "q":
            kettle.sock.close()
            sys.exit(0)
            return
        if user_input == "":
            return
        params = user_input.split()
        if user_input[:2] == "on":
            if len(params) == 1:
                kettle.turn_on()
            elif len(params) == 2:
                kettle.turn_on(int(params[1]))
        elif user_input == "off":
            kettle.turn_off()
        elif user_input == "wake":
            kettle.wake()
        elif user_input == "s":
            print("s: ", kettle.status_json())
        elif user_input == "ss":
            print("ss: ", kettle.stat)
        elif user_input[:3] == "k":
            kettle_socket.keep_connect()
        elif user_input[:3] == "sl:":
            kettle.sock.send(bytes(user_input[3:].encode()))
        elif user_input[:3] == "sm:":
            kettle.sock.send_enc(user_input[3:], SEND_ENCRYPTED)
        else:
            print("Input not recognised:", user_input)

    signal.signal(signal.SIGINT, cb_signal_handler)
    timestamp = time.time()

    if host_port[0] is None:
        kettle.sock.close()
        print("Run again with all parameters - exiting")
        sys.exit(0)
        return

    while True:
        if not kettle_socket.connected:
            kettle_socket.connect(host_port)
            if kettle_socket.connected:
                print("Connected successfully to socket on host", host_port[0])
            else:
                print("Could not connect to socket on host", host_port[0])
                continue

        inout = [kettle_socket.sock]
        infds, outfds, errfds = select.select(inout, inout, [], 120)

        if len(infds) != 0:
            current_power = kettle.stat.get("power")
            current_cmd = kettle.stat.get("cmd")

            k_msg = kettle_socket.receive()
            kettle.update_status(k_msg)

            if current_power != kettle.stat.get("power") and current_cmd != kettle.stat.get("power"):
                print("power changed: ", kettle.stat.get("power"))
                if mqttc is not None:
                    mqttc.publish(MQTT_COMMAND_TOPIC + "/power", kettle.stat.get("power"))

            if mqttc is not None:
                mqttc.publish(MQTT_STATUS_TOPIC + "/STATE", kettle.status_json())
                for i in [
                    "temperature",
                    "target_temp",
                    "set_target_temp",
                    "status",
                    "power",
                    "version",
                    "keep_warm_secs",
                    "keep_warm_onoff",
                    "volume",
                ]:
                    if i in kettle.stat:
                        mqttc.publish(MQTT_STATUS_TOPIC + "/" + i, kettle.stat[i])

        if time.time() - timestamp > MSG_KEEP_CONNECT_FREQ_SECS:
            kettle_socket.keep_connect()
            timestamp = time.time()

        time.sleep(0.2)

def argparser():
    parser = argparse.ArgumentParser()
    parser.add_argument("host", nargs="?", help="kettle host or IP")
    parser.add_argument("imei", nargs="?", help="kettle IMEI (e.g. GD0-12300-35aa)")
    parser.add_argument("--port", help="kettle port (default 6002)", default=6002, type=int)
    parser.add_argument(
        "--mqtt",
        help="MQTT broker host, port, username & password (e.g. --mqtt 192.168.0.1 1883 mqtt_user p@55w0Rd)",
        nargs=4,
        metavar=("host", "port", "username", "password"),
    )
    parser.add_argument(
        "--calibrate",
        help="Min and max volume values for the kettle water level sensor (e.g. --calibrate 160 1640)",
        nargs=2,
        metavar=("lvl_min", "lvl_max"),
    )
    args = parser.parse_args()
    main_loop((args.host, args.port), args.imei, args.mqtt, args.calibrate)

if __name__ == "__main__":
    argparser()
