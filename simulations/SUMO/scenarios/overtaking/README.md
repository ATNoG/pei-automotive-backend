# Overtaking Evaluation Scenario Pack

Simulação de **ultrapassagens autónomas** usando SUMO 1.26 + TraCI + Python.  
Estrutura espelhada no cenário `lanemerge/` — os mesmos padrões de ficheiros
e a mesma interface `pack.py` compatível com `eval.py`.

## Pré-requisitos

```bash
pip install eclipse-sumo traci sumolib paho-mqtt
```

> O `eclipse-sumo` inclui os binários `sumo` e `sumo-gui`; não é necessário
> instalar o SUMO separadamente.

## Estrutura de ficheiros

```
overtaking/
├── network/
│   ├── overtaking.nod.xml   # Nós (START, END)
│   ├── overtaking.edg.xml   # Aresta highway, 2 lanes, 1 km, 130 km/h
│   ├── overtaking.net.xml   # Rede compilada (gerada por netconvert)
│   ├── overtaking.sumocfg   # Configuração SUMO base
│   └── generate.sh          # Regenera net.xml a partir dos primitivos
├── scenarios/
│   ├── scenario_01.rou.xml  # Veículo lento isolado
│   ├── scenario_02.rou.xml  # Bloqueador em lane 1
│   ├── scenario_03.rou.xml  # Comboio de 2 veículos lentos
│   ├── scenario_04.rou.xml  # Diferencial de velocidade pequeno
│   └── scenario_05.rou.xml  # Janela de tráfego em lane 1
├── overtaking_controller.py # Máquina de estados de ultrapassagem
├── pack.py                  # Interface com eval.py (mirrors lanemerge/pack.py)
└── run_overtaking.py        # Runner standalone (headless + GUI)
```

## Execução

```bash
# Headless (validação rápida)
python run_overtaking.py --scenario 01

# Com GUI SUMO
python run_overtaking.py --scenario 01 --gui

# Debug completo
python run_overtaking.py --scenario 03 --log-level DEBUG
```

## Cenários

| ID | Descrição | Estado esperado |
|----|-----------|-----------------|
| 01 | Veículo lento isolado 60 m à frente | Ultrapassagem completa |
| 02 | Bloqueador em lane 1 — aguarda janela | Ultrapassagem após espera |
| 03 | Comboio de 2 veículos lentos | Ultrapassagens sequenciais |
| 04 | Diferencial de 10 km/h | Ultrapassagem marginal |
| 05 | Tráfego em lane 1 — janela temporizada | Ultrapassagem na janela segura |

## Lógica do controlador (`overtaking_controller.py`)

Máquina de estados com 5 estados:

```
CRUISING -> FOLLOWING -> CHANGING_L -> OVERTAKING -> CHANGING_R -> CRUISING
```

**Critérios de segurança (baseados em TTC):**
- TTC mínimo para mudança de faixa: **4 segundos**
- Scan frontal na lane alvo: **150 m**
- Scan traseiro na lane alvo: **40 m**
- Gap de retorno à lane 0: **20 m à frente do veículo ultrapassado**

## Regenerar a rede

```powershell
$netconv = ".\.venv\Lib\site-packages\sumo\bin\netconvert.exe"
& $netconv `
  --node-files network\overtaking.nod.xml `
  --edge-files network\overtaking.edg.xml `
  --output-file network\overtaking.net.xml `
  --no-turnarounds true
```
