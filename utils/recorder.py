#!/usr/bin/python3

import os
import ssl
import json
import time
import random
import csv
import paho.mqtt.client as mqtt
from datetime import datetime

station_id = 21
broker_address = "es-broker.av.it.pt"
broker_port = 8884
broker_client_id = "station_recorder_grafana_{}".format(station_id)
broker_certfile_path = "/etc/it2s/mqtt/admin.crt"
broker_keyfile_path = "/etc/it2s/mqtt/admin.key"
broker_cafile_path = "/etc/it2s/mqtt/ca.crt"
broker_sub_topic_station = "its_center/inqueue/json/{}/#".format(station_id)
broker_sub_topic_logs = "logs/{}/#".format(station_id)
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, broker_client_id)

recordings_dir = "./recordings"
recordings_file = os.path.join(recordings_dir, "recording_station{}_{}.csv".format(station_id, datetime.now().strftime("%d_%m_%Y_%H_%M_%S")))

def on_message(_client, userdata, message):
    try:
        topic = message.topic
        payload = message.payload.decode("utf-8").replace("\n", "")
        timestamp = time.time()

        with open(recordings_file, mode="a", newline="") as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow([timestamp, topic, payload])

        print(f"Recorded: {timestamp}, {topic}, {payload}")
    except Exception as e:
        print(f"Error writing to CSV: {e}")

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("Failed to connect.")
    client.subscribe([(broker_sub_topic_station, 0), (broker_sub_topic_logs, 0)])

def main():
    client.username_pw_set(username="admin", password="t;RHC_vi")
    client.tls_set(
        certfile=broker_certfile_path,
        keyfile=broker_keyfile_path,
        ca_certs=broker_cafile_path,
    )

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker_address, broker_port)

    if not os.path.exists(recordings_file):
        with open(recordings_file, mode="w", newline="") as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow(["Timestamp", "Topic", "Payload"])

    client.loop_forever()

main()
