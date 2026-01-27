import csv
import json

# CSV file settings
csv_num_lines = 400
csv_file_name = "./recordings/generated_barra_cpm_lanemerge.csv"
csv_topic_name = "its_center/inqueue/json/88/CPM"

# Time settings
base_timestamp = 0
incr_timestamp = 0.1

# RSU settings
ref_station_lat = 40.628042
ref_station_lon = -8.732658

# Detected objects starting distance to RSU
object0_xDistances_start = 25200
object0_yDistances_start = -11450
object1_xDistances_start = 25000
object1_yDistances_start = -11850
object2_xDistances_start = 15000
object2_yDistances_start = 7000

# Coeficient for calculation of the object's distance over time to the RSU - for every unit of y, this the the x value that is incremented
object0_coef_distance = -2.118
object1_coef_distance = -2.118
object2_coef_distance = -2.118

# Detected objects speed (only used to calculate distance to RSU over time)
object0_coef_speed = 60
object1_coef_speed = 80
object2_coef_speed = 60

# Number of entries needed for object starting to move
object0_startup_time = 0
object1_startup_time = 40
object2_startup_time = 0

# Initialize object's position arrays
object0_xDistances = [object0_xDistances_start] * csv_num_lines
object0_yDistances = [object0_yDistances_start] * csv_num_lines
object1_xDistances = [object1_xDistances_start] * csv_num_lines
object1_yDistances = [object1_yDistances_start] * csv_num_lines
object2_xDistances = [object2_xDistances_start] * csv_num_lines
object2_yDistances = [object2_yDistances_start] * csv_num_lines

# Calculate object's positions over time
for i in range(csv_num_lines-1):

    # Straight Line
    if (i > object0_startup_time):
        object0_xDistances[i+1] = round(object0_xDistances[i] +object0_coef_distance*object0_coef_speed, 5)
        object0_yDistances[i+1] = round(object0_yDistances[i] +1*object0_coef_speed, 5)

    # Straight Line
    if (i > object1_startup_time):
        object1_xDistances[i+1] = round(object1_xDistances[i] +object1_coef_distance*object1_coef_speed, 5)
        object1_yDistances[i+1] = round(object1_yDistances[i] +1*object1_coef_speed, 5)

    # Lane Merge + Straight Line
    if (i > object2_startup_time):
        if (i < object2_startup_time + 15):
            object2_xDistances[i+1] = object2_xDistances[i]-15
            object2_yDistances[i+1] = object2_yDistances[i]-60
        elif (i < object2_startup_time + 55):
            object2_xDistances[i+1] = object2_xDistances[i]-30
            object2_yDistances[i+1] = object2_yDistances[i]-60
        elif (i < object2_startup_time + 100):
            object2_xDistances[i+1] = object2_xDistances[i]-60
            object2_yDistances[i+1] = object2_yDistances[i]-60
        elif (i < object2_startup_time + 130):
            object2_xDistances[i+1] = object2_xDistances[i]-90
            object2_yDistances[i+1] = object2_yDistances[i]-50
        elif (i < object2_startup_time + 160):
            object2_xDistances[i+1] = object2_xDistances[i]-100
            object2_yDistances[i+1] = object2_yDistances[i]-10
        elif (i < object2_startup_time + 190):
            object2_xDistances[i+1] = object2_xDistances[i]-100
            object2_yDistances[i+1] = object2_yDistances[i]+10
        else:
            object2_xDistances[i+1] = round(object2_xDistances[i] +object2_coef_distance*object2_coef_speed, 5)
            object2_yDistances[i+1] = round(object2_yDistances[i] +1*object2_coef_speed, 5)

csv_structure = [
    "Timestamp", "Topic", "Payload"
]

cpm_structure = {
    "header": {
        "protocolVersion": 10,
        "messageID": 14,
        "stationID": 88
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
                        "value": object0_xDistances[0], 
                        "confidence": 102
                    },
                    "yDistance": {
                        "value": object0_yDistances[0], 
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
                        "value": object1_xDistances[0], 
                        "confidence": 102
                    },
                    "yDistance": {
                        "value": object1_yDistances[0], 
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
                        "value": object2_xDistances[0], 
                        "confidence": 102
                    },
                    "yDistance": {
                        "value": object2_yDistances[0], 
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
    
    payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][0]["xDistance"]["value"] = object0_xDistances[i]
    payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][0]["yDistance"]["value"] = object0_yDistances[i]
    
    payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][1]["xDistance"]["value"] = object1_xDistances[i]
    payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][1]["yDistance"]["value"] = object1_yDistances[i]
    
    payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][2]["xDistance"]["value"] = object2_xDistances[i]
    payload["cpm"]["cpmParameters"]["perceivedObjectContainer"][2]["yDistance"]["value"] = object2_yDistances[i]

    csv_lines.append([timestamp, csv_topic_name, json.dumps(payload)])

with open(csv_file_name, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(csv_structure)
    writer.writerows(csv_lines)
