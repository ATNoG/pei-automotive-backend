#!/usr/bin/python3

import ssl
import json
import time
import random
import csv
import paho.mqtt.client as mqtt

broker_address = "es-broker.av.it.pt"
broker_port = 8884
broker_client_id = "recording_publisher_mqtt"
broker_certfile_path = "/etc/it2s/mqtt/admin.crt"
broker_keyfile_path = "/etc/it2s/mqtt/admin.key"
broker_cafile_path = "/etc/it2s/mqtt/ca.crt"
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, broker_client_id)
recording_file = "./recordings/generated_barra_cpm_lanemerge.csv"

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("failed to connect")

def main():
    client.username_pw_set(username="admin", password="t;RHC_vi")
    client.tls_set(
        certfile=broker_certfile_path,
        keyfile=broker_keyfile_path,
        ca_certs=broker_cafile_path,
    )

    client.on_connect = on_connect
    client.connect(broker_address, broker_port)
    client.loop_start()

    timestamps = []
    topics = []
    payloads = []
    file_size = 0

    with open(recording_file, 'r') as file:
        csv_data = file.read()
    reader = csv.DictReader(csv_data.splitlines())
    for row in reader:
        timestamps.append(float(row["Timestamp"]))
        topics.append(row["Topic"])
        payloads.append(json.loads(row["Payload"].replace('""', '"')))
        file_size = file_size +1

    print("csv size: " +str(file_size))
    i = 0
    while True:
        if (i == file_size-1):
            i = 0

        current_timestamp = timestamps[i]
        next_timestamp = timestamps[i+1]
        sleep_duration = next_timestamp - current_timestamp

        current_topic = topics[i]
        current_payload = payloads[i]
        client.publish(current_topic, json.dumps(current_payload))
        print("sending {} to: {}".format(i, current_topic))

        i = i+1
        time.sleep(sleep_duration)

    client.loop_stop()

main()
