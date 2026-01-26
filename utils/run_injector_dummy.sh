#!/bin/bash

python -m venv venv-name
source venv-name/bin/activate
pip install paho-mqtt
python injector_dummy.py
