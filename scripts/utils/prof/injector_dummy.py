#!/usr/bin/python3

import ssl
import json
import time
import random
import paho.mqtt.client as mqtt

station_id = 21
broker_address = "es-broker.av.it.pt"
broker_port = 8884
broker_client_id = "dummy_publisher_mqtt_{}".format(station_id)
broker_certfile_path = "/etc/it2s/mqtt/admin.crt"
broker_keyfile_path = "/etc/it2s/mqtt/admin.key"
broker_cafile_path = "/etc/it2s/mqtt/ca.crt"

broker_delay_pub_topic = "logs/{}/DELAY".format(station_id)
broker_throughput_pub_topic = "logs/{}/THROUGHPUT".format(station_id)
broker_vsm_pub_topic = "its_center/inqueue/json/{}/VSM".format(station_id)

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, broker_client_id)

def pub_delay_message(counter):
    delay_avg = 20
    delay_offset = 0

    if (counter % 8 == 0):
        delay_offset = random.randint(-5, 5)

    msg_inst = {
        "message_delay": delay_avg + delay_offset
    }

    client.publish(broker_delay_pub_topic, json.dumps(msg_inst))

def pub_throughput_message(counter):
    base_tx = 2000
    base_rx = 4000
    base_offset = 0

    if (counter % 3 == 0):
        base_offset = random.randint(0, 1)*100

    msg_inst = {
        "rx_bytes": base_tx + base_offset,
        "tx_bytes": base_rx + base_offset
    }

    client.publish(broker_throughput_pub_topic, json.dumps(msg_inst))

def pub_vsm_message(counter):
    latitude = 406344570
    longitude = -86598500
    mcc = 3
    mnc = 268
    ratmode = 12
    pci = 297

    rsrq_base = -14
    rsrq_offset = 0
    rsrp_base = -102
    rsrp_offset = 0
    snr_base = 40
    snr_offset = 0
    speed_base = 0.0
    speed_offset = 0.0

    if (counter % 3 == 0):
        snr_offset = random.randint(-1, 1)*5
        speed_offset = random.random()

    msg_inst = {
        "vsm": {
            "referencePosition": {
                "latitude": latitude,
                "longitude": longitude
            },
            "modemStatus": {
                "mcc": mcc,
                "mnc": mnc,
                "ratMode": ratmode,
                "nr": {
                    "rsrq": rsrq_base + rsrq_offset,
                    "rsrp": rsrp_base + rsrp_offset,
                    "snr": snr_base + snr_offset,
                    "pci": pci
                }
            },
            "oBody": {
                "sensorsDataContainer": {
                    "speedDataContainer": {
                        "speed": speed_base + speed_offset
                    }
                }
            }
        }
    }

    client.publish(broker_vsm_pub_topic, json.dumps(msg_inst))

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("failed to connect")

def main():
    last_delay_ms = 0
    last_throughput_ms = 0
    last_vsm_ms = 0
    delay_counter = 0
    throughtput_counter = 0
    vsm_counter = 0

    client.username_pw_set(username="admin", password="t;RHC_vi")
    client.tls_set(
        certfile=broker_certfile_path,
        keyfile=broker_keyfile_path,
        ca_certs=broker_cafile_path,
    )

    client.on_connect = on_connect
    client.connect(broker_address, broker_port)

    client.loop_start()
    while True:
        current_timestamp_ms = round(time.time() * 1000)
        delay_time_diff = current_timestamp_ms - last_delay_ms
        throughput_time_diff = current_timestamp_ms - last_throughput_ms
        vsm_time_diff = current_timestamp_ms - last_vsm_ms
        
        if (delay_time_diff > 100):
            print("Sending DELAY...")
            pub_delay_message(delay_counter)
            last_delay_ms = current_timestamp_ms
            delay_counter = delay_counter +1

        if (throughput_time_diff > 1000):
            print("Sending THROUGHPUT...")
            pub_throughput_message(throughtput_counter)
            last_throughput_ms = current_timestamp_ms
            throughtput_counter = throughtput_counter +1

        if (vsm_time_diff > 400):
            print("Sending VSM...")
            pub_vsm_message(vsm_counter)
            last_vsm_ms = current_timestamp_ms
            vsm_counter = vsm_counter +1

        time.sleep(0.05)

    client.loop_stop()

main()
