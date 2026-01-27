import csv
import json

# CSV file settings
csv_num_lines = 400
csv_file_name = "./recordings/generated_barra_cpm_overtaking.csv"
csv_topic_name = "its_center/inqueue/json/87/CPM"

# Timestamp settings
base_timestamp = 0
incr_timestamp = 0.1

# RSU settings
ref_station_lat = 40.628042
ref_station_lon = -8.732658

# Detected objects starting distance to RSU
object0_base_xDistance = 0.0
object0_base_yDistance = 480.0
object1_base_xDistance = 0.0
object1_base_yDistance = 45.0
object2_base_xDistance = 2118
object2_base_yDistance = -520

# Coeficient for calculation of the object's distance over time to the RSU - for every unit of y, this the the x value that is incremented
object0_coef_distance = -2.118
object1_coef_distance = -2.118
object2_coef_distance = -2.118

# Detected objects speed (only used to calculate distance to RSU over time)
object0_coef_speed = 70
object1_coef_speed = 90
object2_coef_speed = 70

# Number of entries needed for object starting to move
object0_startup_time = 0
object1_startup_time = 40
object2_startup_time = 0

csv_structure = [
    "Timestamp", "Topic", "Payload"
]

cpm_structure = {
    "header": {
        "protocolVersion": 10,
        "messageID": 14,
        "stationID": 87
    },
    "cpm": {
        "generationDeltaTime": 0,
        "cpmParameters": {
            "managementContainer": {
                "stationType": 15,
                "referencePosition": {
                    "latitude": int(ref_station_lat * 10000000),
                    "longitude": int(ref_station_lon * 10000000),
                    "positionConfidenceEllipse": {
                        "semiMajorConfidence": 100,
                        "semiMinorConfidence": 100,
                        "semiMajorOrientation": 0
                    },
                    "altitude": {
                        "altitudeValue": 200, 
                        "altitudeConfidence": "unavailable"
                    }
                }
            },
            "perceivedObjectContainer": [
                {
                    "objectID": 0,
                    "timeOfMeasurement": 0,
                    "objectConfidence": 95,
                    "xDistance": {
                        "value": object0_base_xDistance, 
                        "confidence": 102
                    },
                    "yDistance": {
                        "value": object0_base_yDistance, 
                        "confidence": 102
                    },
                    "xSpeed": {
                        "value": 0,
                        "confidence": 40
                    },
                    "ySpeed": {
                        "value": 0,
                        "confidence": 40
                    },
                    "objectRefPoint": 4,
                    "classification": [
                        {
                            "confidence": 0,
                            "class": {
                                "vehicle": {
                                    "type": 5, 
                                    "confidence": 0
                                }
                            }
                        }
                    ]
                },
                {
                    "objectID": 1,
                    "timeOfMeasurement": 0,
                    "objectConfidence": 95,
                    "xDistance": {
                        "value": object1_base_xDistance, 
                        "confidence": 102
                    },
                    "yDistance": {
                        "value": object1_base_yDistance, 
                        "confidence": 102
                    },
                    "xSpeed": {
                        "value": 0,
                        "confidence": 40
                    },
                    "ySpeed": {
                        "value": 0,
                        "confidence": 40
                    },
                    "objectRefPoint": 4,
                    "classification": [
                        {
                            "confidence": 0,
                            "class": {
                                "vehicle": {
                                    "type": 3, 
                                    "confidence": 0
                                }
                            }
                        }
                    ]
                },
                {
                    "objectID": 2,
                    "timeOfMeasurement": 0,
                    "objectConfidence": 95,
                    "xDistance": {
                        "value": object2_base_xDistance, 
                        "confidence": 102
                    },
                    "yDistance": {
                        "value": object2_base_yDistance, 
                        "confidence": 102
                    },
                    "xSpeed": {
                        "value": 0,
                        "confidence": 40
                    },
                    "ySpeed": {
                        "value": 0,
                        "confidence": 40
                    },
                    "objectRefPoint": 4,
                    "classification": [
                        {
                            "confidence": 0,
                            "class": {
                                "vehicle": {
                                    "type": 3, 
                                    "confidence": 0
                                }
                            }
                        }
                    ]
                }
            ],
            "numberOfPerceivedObjects": 3
        }
    }
}

csv_lines = []
for i in range(csv_num_lines):
    timestamp = round(base_timestamp + (i * incr_timestamp), 2)
    payload = json.loads(json.dumps(cpm_structure))
    
    if i > object0_startup_time:
        payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][0]["xDistance"]["value"] += round(object0_coef_distance * object0_coef_speed * (i - object0_startup_time), 5)
        payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][0]["yDistance"]["value"] += round(1 * object0_coef_speed * (i - object0_startup_time), 5)
    
    if i > object1_startup_time:
        payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][1]["xDistance"]["value"] += round(object1_coef_distance * object1_coef_speed * (i - object1_startup_time), 5)
        payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][1]["yDistance"]["value"] += round(1 * object1_coef_speed * (i - object1_startup_time), 5)
    
    if i > object2_startup_time:
        payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][2]["xDistance"]["value"] += round(object2_coef_distance * object2_coef_speed * (i - object2_startup_time), 5)
        payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][2]["yDistance"]["value"] += round(1 * object2_coef_speed * (i - object2_startup_time), 5)

    csv_lines.append([timestamp, csv_topic_name, json.dumps(payload)])

with open(csv_file_name, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(csv_structure)
    writer.writerows(csv_lines)
