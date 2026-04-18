# SUMO Simulation - Ponte da Barra


SUMO simulation of Ponte da Barra, includes the road network, generated traffic demand, configuration files.

## Running the sim

Needs SUMO and python  ```traci```  library.

https://sumo.dlr.de/docs/Installing/index.html

Run the simulation with GUI (for visualization):

``` bash
sumo-gui -c osm.sumocfg
```

Run without GUI:

``` bash
sumo -c osm.sumocfg
```

Run with Python (TraCI) to access a running sim:

``` bash
python3 simulation.py
```

## files

    barraSUMO/
    ├── config/
    │   ├── osm.view.xml          # view settings for GUI
    │   └── output.add.xml        # additional file to define output
    ├── networkfile/
    │   └── barraOSM.net.xml      # road net file (edges, junctions, lanes) 
    ├── output/                    
    │   ├── edgeData.xml          # Data per edge
    │   ├── stats.xml             # Simulation summary
    │   └── tripinfos.xml         # Trip information
    ├── routes/
    │   ├── random.sh             # generate random trips and routes                         
    │   ├── routes.rou.xml        # routes generated (used in sim)
    │   ├── trips.trips.xml       # trips generated 
    │   └── vehicledist.rou.xml   # vehicle distribution (example by hand)
    ├── osm.sumocfg               # config file of sim
    ├── simulation.py             # TraCi python file
    └── README.md

## Steps to build the sim.

### Network Generation

Generated with:

``` bash
python3 /usr/share/sumo/tools/osmWebWizard.py
```

https://sumo.dlr.de/docs/Tutorials/OSMWebWizard.html

Edit network:

``` bash
sumo-gui -n file.net.xml
```

Press Ctrl+T to edit in NetEdit.

Save it as ```networkfile/barraOSM.net.xml```


https://sumo.dlr.de/docs/Netedit/index.html#editing_modes

### Traffic Demand

We have roads now we need to create vehicles to drive in them. An easy way is to do it random.

``` bash
./routes/random.sh
```

-   1 hour simulation
-   1000 vehicles/hour

https://sumo.dlr.de/docs/Tools/Trip.html#randomtripspy

You can add a vehicle distribution to have different vehcile types in the sim ```vehicledist.rou.xml```.

### Running the sim

We now have everything to run a simple sim on sumo.
Put all files needed to run sim in a config file ```osm.sumocfg```. Then run it as described above with gui or not.

Can also use Traci to access a running traffic simulation, example file ```simulation.py``` to test this.

https://sumo.dlr.de/docs/TraCI/index.html

> more things you can do: add induction loops in a lane to count vehicles...

> https://sumo.dlr.de/docs/

## More info on outputs

Edge data:

Visualize the edge data with sumo-gui

https://sumo.dlr.de/docs/Tools/Visualization.html

Trip info:

https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html

Stats:

https://sumo.dlr.de/docs/Simulation/Output/StatisticOutput.html


