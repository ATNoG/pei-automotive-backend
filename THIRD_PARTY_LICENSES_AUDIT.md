# Auditoria de Licenças de Software de Terceiros

## Objetivo
Documentar de forma concisa todas as dependências de terceiros e recursos externos usados pelo projeto, para suporte a revisão de licenças e conformidade.

## Escopo obrigatório
Incluir:
- Bibliotecas Python usadas diretamente pelo projeto
- Dependências externas referenciadas no código e na configuração
- Imagens Docker externas e serviços de infraestrutura
- URLs e APIs externas consumidas pelo backend

## Dependências Python principais
Baseado em `requirements.txt` e no código fonte, devem ser listadas todas as bibliotecas de terceiros:
- websocket-client
- paho-mqtt
- python-dotenv
- pytest
- requests
- certifi
- geopy
- eclipse-sumo
- traci
- sumolib
- fastapi
- uvicorn[standard]
- pydantic
- pydantic-settings
- PyJWT
- httpx
- testcontainers[postgres]
- docker
- pg8000

Além das acima, usar também no projeto:
- asyncpg

## Imagens e componentes de infraestrutura externos
Incluir as imagens externas usadas em `docker-compose.yml`:
- eclipse-mosquitto:2
- postgres:16
- quay.io/keycloak/keycloak:24.0
- imagens hospedadas em `atnog-harbor.av.it.pt/pei-2025-automotive-app/...`

## Ferramentas e serviços externos referenciados
Listar serviços e APIs externas consumidos pelo projeto:
- Eclipse Ditto (vias `DITTO_WS_URL`, `DITTO_API_URL`)
- Eclipse Hono (via `HONO_API_URL`)
- `http://tomastest.com` para dados meteorológicos
- Overpass API: `https://overpass-api.de/api/interpreter`
- SUMO / eclipse-sumo para simulação de tráfego
- Docker, Helm, k3s, kubectl (ferramentas de deploy / infraestrutura)

## Tipos de licença comuns a considerar
- MIT — permissiva, permite uso comercial e modificação
- GPL — obriga a distribuir o código-fonte se o software for redistribuído
- Apache 2.0 — permissiva, com proteção de patentes
- Proprietárias — condicionadas ao fornecedor e podem restringir uso/redistribuição

## Cuidados essenciais
- Verificar compatibilidade entre licenças (por exemplo, MIT e GPL podem ser incompatíveis)
- Confirmar se as licenças permitem o uso pretendido: comercial, académico ou interno
- Se há biblioteca GPL usada num produto comercial, pode ser exigido abrir o código-fonte completo
- Incluir também licenças de imagens Docker e componentes de infraestrutura que contenham software de terceiros

## Recomendação de ação
1. Extrair a licença de cada dependência listada em `requirements.txt`.
2. Verificar a licença das imagens Docker externas e dos componentes em `docker-compose.yml`.
3. Analisar conflito de licenças entre todos os componentes utilizados.
4. Documentar resultados em um inventário de licenças separado, com: nome, versão, origem e tipo de licença.

## Observação
Não incluir bibliotecas da linguagem padrão Python (`json`, `logging`, `os`, `pathlib`, etc.), apenas componentes de terceiros e recursos externos.
