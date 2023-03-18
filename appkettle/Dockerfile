ARG BUILD_FROM
FROM $BUILD_FROM

# Install requirements for add-on
RUN \
  apk add --no-cache \
    python3 \
    py3-pip

RUN \
  apk add -U g++ gcc

RUN pip install paho.mqtt \
  pycryptodomex

# Python 3 HTTP Server serves the current working dir
# So let's set it to our add-on persistent data directory.
WORKDIR /data

# Copy data for add-on
COPY appkettle_mqtt.py /
COPY protocol_parser.py /
COPY run.sh /
RUN chmod a+x /appkettle_mqtt.py
RUN chmod a+x /protocol_parser.py
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]