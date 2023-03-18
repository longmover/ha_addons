ARG BUILD_FROM
FROM $BUILD_FROM

# Install requirements for add-on
RUN \
  apk add --no-cache \
    python3 \
    py3-pip \
    gcc \
    libxml2-dev \
    libxslt-dev \
    build-base \
    python3-dev

RUN pip install bs4 \
  paho.mqtt \
  lxml \
  pythonping \
  requests

WORKDIR /data

# Copy data for add-on
COPY ip_pdu.py /
COPY pdu_control.py /
COPY run.sh /
RUN chmod a+x /ip_pdu.py
RUN chmod a+x /pdu_control.py
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
