#!/usr/bin/with-contenv bashio

echo "Starting AppKettle"

#echo "HA token: ${SUPERVISOR_TOKEN}"
kettle_ip=`python3 /appkettle_mqtt.py | grep IP | awk '{print $3}'`
kettle_imei=`python3 /appkettle_mqtt.py | grep IMEI | awk '{print $3}'`

echo "Kettle IP:   $kettle_ip"
echo "Kettle IMEI: $kettle_imei"
mqtt_host=$(bashio::config 'mqtt_host')
mqtt_port=$(bashio::config 'mqtt_port')
mqtt_usr=$(bashio::config 'mqtt_usr')
mqtt_pwd=$(bashio::config 'mqtt_pwd')
min_lvl=$(bashio::config 'min_lvl')
max_lvl=$(bashio::config 'max_lvl')

if [ -z $kettle_ip ]
then
  echo "No kettle detected, ensure kettle cannot access the internet and retry"
else
  python3 /appkettle_mqtt.py --mqtt $mqtt_host $mqtt_port $mqtt_usr $mqtt_pwd $kettle_ip $kettle_imei --calibrate $min_lvl $max_lvl
fi
