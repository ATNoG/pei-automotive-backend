@echo off
python "%SUMO_HOME%\tools\randomTrips.py" -n ..\networkfile\barraOSM.net.xml -r routes.rou.xml --insertion-rate 1000 --trip-attributes="type=\"typedist\" departLane=\"best\" departSpeed=\"max\" departPos=\"random\"" --fringe-factor 10
