# Overtaking Detector – Evaluation Scenario Pack

Avalia o serviço `overtaking_detector` com 10 cenários controlados na rede
do lanemerge.  O resultado é reportado em Precision / Recall / F1 / Accuracy
com decomposição TP / FP / TN / FN.

## Pré-requisitos

Stack completa em execução (Docker Compose):

```bash
docker compose up -d
```

Pacotes Python (venv do projecto):

```bash
pip install eclipse-sumo traci sumolib paho-mqtt requests python-dotenv
```

## Execução

```bash
# Todos os cenários
python scripts/eval.py --pack overtaking

# Cenário específico (modo GUI)
python scripts/eval.py --pack overtaking --scenarios 01 --gui

# Salvar resultado noutro caminho
python scripts/eval.py --pack overtaking --output /tmp/overtaking_eval.json
```

## Estrutura dos cenários

| ID | Tipo | Descrição | Expected |
|----|------|-----------|----------|
| 01 | TP   | Fast overtakes slow (same highway segment) | overtaking |
| 02 | TP   | 80 m gap – delayed but certain overtake | overtaking |
| 03 | TP   | Fast ego overtakes two slower cars sequentially | overtaking |
| 04 | TN   | Identical speed on same route – no sign flip | no_event |
| 05 | TN (FP probe) | Cars on different roads: entering vs highway\_in | no_event |
| 06 | TN (FP probe) | Ramp car enters after highway car has passed | no_event |
| 07 | TP   | Extreme speed differential (120 vs 50 km/h) | overtaking |
| 08 | TN (FN probe) | Ego departs 15 s late at same speed | no_event |
| 09 | TP   | Fast ego overtakes slow + medium car (two events) | overtaking |
| 10 | TN (FP probe) | Two cars on ramp at same speed | no_event |

## Mapeamento de alertas (interpret_alert)

O `overtaking_detector` publica alertas sem campo `status`.  O `pack.py`
expõe `interpret_alert(alert) -> str` que o `eval.py` chama em vez de ler
`alert["status"]`:

```python
"overtaking_event"  →  "overtaking"   (positivo)
None / outro        →  "no_event"     (negativo)
```

## Métricas capturadas

- **Tempo de resposta**: latências HTTP Ditto registadas por `bridge.py`
  (avg_ms, p50_ms, p95_ms) nos logs durante cada cenário.
- **Throughput**: número de updates enviados/recebidos por passo de simulação.
- **Precision / Recall / F1 / Accuracy**: relatório final de classificação.
- **TP / FP / TN / FN**: decomposição scenario a scenario.
