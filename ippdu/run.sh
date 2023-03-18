#!/usr/bin/with-contenv bashio

pdu_host=$(bashio::config 'pdu_host')
mqtt_host=$(bashio::config 'mqtt_host')
mqtt_port=$(bashio::config 'mqtt_port')
mqtt_usr=$(bashio::config 'mqtt_usr')
mqtt_pwd=$(bashio::config 'mqtt_pwd')

echo "PDU target:  $pdu_host"
echo "mqtt server: $mqtt_host"
echo "mqtt port:   $mqtt_port"
echo "mqtt user:   $mqtt_usr"

python3 /pdu_control.py $pdu_host --mqtt $mqtt_host $mqtt_port $mqtt_usr $mqtt_pwd