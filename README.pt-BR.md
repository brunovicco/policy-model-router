# Policy Model Router

[![Quality](https://github.com/brunovicco/policy-model-router/actions/workflows/quality.yml/badge.svg)](https://github.com/brunovicco/policy-model-router/actions/workflows/quality.yml)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)

Read this in [English](README.md).

Um serviço de roteamento determinístico e *fail-closed* que seleciona um grupo de modelo aprovado
para uma carga de trabalho de LLM antes da inferência.

O roteador mantém a escolha do modelo fora dos prompts dos agentes e do código de aplicação. Um
chamador descreve a carga de trabalho, a classificação de dados, o tamanho de contexto e os
limites operacionais; `POST /route` avalia essa requisição contra uma política versionada e
retorna um registro de decisão explicável ou uma rejeição explícita. O serviço não chama um LLM.

## Por que este projeto existe

Um sistema de IA corporativo costuma ter vários deployments de modelo com diferentes autorizações
de dados, capacidades, janelas de contexto, perfis de latência e custos. Deixar cada agente
escolher um modelo por conta própria torna essas decisões difíceis de governar, reproduzir e
auditar.

O Policy Model Router centraliza essa fronteira:

- os mapeamentos de carga de trabalho para grupo de modelo são declarativos e versionados em
  [`config/routing_policy.yaml`](config/routing_policy.yaml);
- restrições rígidas eliminam grupos inelegíveis em uma ordem fixa;
- a mesma requisição e a mesma política sempre produzem o mesmo grupo selecionado e os mesmos
  motivos de rejeição;
- todo grupo não selecionado é incluído na decisão com uma explicação;
- políticas inválidas ou incompletas falham de forma fechada (*fail-closed*) em vez de degradar
  silenciosamente;
- a API retorna envelopes de erro estáveis e legíveis por máquina.

O valor selecionado é um grupo de modelo lógico, como `reasoning-medium`, não um provedor ou um
deployment específico. A seleção de provedor, o failover, as credenciais e a chamada de inferência
em si pertencem a um gateway de modelos posterior na cadeia.

## Como o roteamento funciona

```mermaid
flowchart TD
    A["POST /route"] --> B["Validar contrato fechado"]
    B --> C["Carregar mapeamento da carga de trabalho"]
    C --> D["Avaliar todo grupo contra as restrições ordenadas"]
    D --> E{"Grupo mapeado é viável?"}
    E -->|Sim| F["Retornar decisão e motivos de rejeição"]
    E -->|Não| G["Retornar rejeição explícita 422"]
```

Para cada requisição, o caso de uso da camada de aplicação:

1. Localiza o grupo de modelo mapeado para a carga de trabalho requisitada.
2. Avalia todos os grupos configurados contra as restrições abaixo, parando na primeira falha para
   cada candidato.
3. Seleciona o grupo mapeado somente se ele sobreviver a todas as restrições.
4. Reporta todo outro grupo como rejeitado, seja porque falhou em uma restrição, seja porque a
   carga de trabalho mapeia para outro grupo.
5. Rejeita a requisição se o grupo mapeado for inelegível. A versão atual não substitui por um
   grupo diferente nem aplica uma pontuação ponderada.

IDs de decisão e timestamps são gerados em tempo de execução; a seleção do grupo de modelo e os
motivos são a parte determinística do resultado.

### Ordem das restrições

A ordem importa porque a primeira restrição que falha se torna o motivo de rejeição daquele
candidato.

| # | Restrição | O candidato é rejeitado quando |
|---:|---|---|
| 1 | Classificação de dados | O grupo não é autorizado para a classificação da requisição |
| 2 | Nível de risco | O grupo não é autorizado para o nível de risco do fluxo de trabalho da requisição |
| 3 | Saída estruturada | A requisição exige saída estruturada e o grupo não suporta |
| 4 | Chamada de ferramentas | A carga de trabalho exige *tool calling* e o grupo não suporta |
| 5 | Janela de contexto | Os tokens de entrada estimados excedem o limite do grupo |
| 6 | Teto de custo | O custo estimado do grupo excede `max_cost_usd` |
| 7 | Teto de latência | A latência típica do grupo excede `max_latency_ms` |
| 8 | Disponibilidade | O provedor resolve o grupo como indisponível (veja [Disponibilidade](#disponibilidade)) |
| 9 | Lista de agentes permitidos | O grupo é restrito e o agente requisitante não está na lista |

Os predicados vivem em
[`src/policy_model_router/domain/constraints.py`](src/policy_model_router/domain/constraints.py),
e o algoritmo de seleção em dois passos vive em
[`src/policy_model_router/application/route_model.py`](src/policy_model_router/application/route_model.py).

## Política incluída

O repositório inclui uma política de exemplo para cinco tipos de carga de trabalho e quatro grupos
de modelo lógicos. Os valores são entradas de política de deployment, não medições em tempo real de
provedor.

### Mapeamentos de carga de trabalho

| Carga de trabalho | Grupo de modelo mapeado | Exige *tool calling* nativo |
|---|---|---:|
| `document_extraction` | `fast-small` | Não |
| `cashflow_analysis` | `reasoning-medium` | Não |
| `findings_correlation` | `reasoning-strong` | Não |
| `opinion_drafting` | `reasoning-strong` | Não |
| `json_repair` | `fast-structured-output` | Não |

### Perfis dos grupos de modelo

| Grupo de modelo | Dados autorizados | Risco autorizado | Saída estruturada | Tool calling | Contexto | Latência típica | Custo estimado |
|---|---|---|---:|---:|---:|---:|---:|
| `fast-small` | public, internal | low, medium | Não | Sim | 16.000 | 3.000 ms | USD 0,01 |
| `reasoning-medium` | public, internal, confidential, restricted | low, medium, high | Não | Sim | 64.000 | 15.000 ms | USD 0,05 |
| `reasoning-strong` | public, internal, confidential, restricted | low, medium, high, critical | Não | Sim | 128.000 | 30.000 ms | USD 0,20 |
| `fast-structured-output` | public, internal | low, medium | Sim | Não | 8.000 | 2.000 ms | USD 0,01 |

A coluna de risco autorizado reflete uma regra de qualidade da decisão, não de proteção de dados:
um grupo pode estar totalmente autorizado para os dados envolvidos e ainda assim não ser autorizado
para uma decisão de alto risco (veja a [emenda da ADR-0005](docs/adr/0005-deterministic-policy-routing.md)).
Os quatro grupos estão marcados como disponíveis e sem restrição de lista de agentes na política
incluída. Altere esses valores deliberadamente para cada ambiente.

## Início rápido

Requisitos: Python 3.13 e [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brunovicco/policy-model-router.git
cd policy-model-router
uv sync --frozen
export API_KEYS='{"credit-analysis-agent":"dev-local-key"}'   # obrigatório; indexado por agent_name
uv run uvicorn policy_model_router.entrypoints.http:app --reload
```

O serviço inicia em `http://127.0.0.1:8000` e carrega `config/routing_policy.yaml` uma única vez
na inicialização. Toda chamada a `POST /route` precisa do header `X-API-Key` mostrado abaixo; veja
[Autenticação e rate limiting](#autenticação-e-rate-limiting).

### Solicitando uma decisão

Esta requisição contém dados restritos e um contexto de 100.000 tokens, então apenas o grupo
`reasoning-strong` mapeado para a carga de trabalho permanece viável:

```bash
curl --request POST http://127.0.0.1:8000/route \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: dev-local-key' \
  --data '{
    "schema_version": "1.0",
    "requested_at": "2026-07-22T12:00:00Z",
    "workflow_id": "credit-review-42",
    "task_id": "correlate-findings-7",
    "agent_name": "credit-analysis-agent",
    "workload": "findings_correlation",
    "risk_level": "high",
    "data_classification": "restricted",
    "context_tokens_estimated": 100000,
    "structured_output_required": false,
    "max_latency_ms": 60000,
    "max_cost_usd": 1.00
  }'
```

Exemplo de resposta:

```json
{
  "schema_version": "1.0",
  "routing_decision_id": "674088f4-cd75-45e9-a6b5-5e85b8cc5588",
  "decided_at": "2026-07-22T12:00:01Z",
  "workflow_id": "credit-review-42",
  "task_id": "correlate-findings-7",
  "selected_model_group": "reasoning-strong",
  "reason": "workload 'findings_correlation' maps to model group 'reasoning-strong' and satisfies all constraints",
  "rejected_candidates": [
    {
      "model_group": "fast-small",
      "reason": "not authorized for data classification 'restricted'"
    },
    {
      "model_group": "fast-structured-output",
      "reason": "not authorized for data classification 'restricted'"
    },
    {
      "model_group": "reasoning-medium",
      "reason": "estimated context 100000 tokens exceeds group limit of 64000 tokens"
    }
  ]
}
```

### Rejeição rígida

`document_extraction` mapeia para `fast-small`, que não é autorizado para dados confidenciais na
política incluída. O roteador não promove silenciosamente a requisição para um grupo mais forte:

```json
{
  "error": {
    "code": "no_viable_model_group",
    "message": "no viable model group for workload 'document_extraction': mapped group 'fast-small' rejected (not authorized for data classification 'confidential')"
  }
}
```

O status da resposta é `422 Unprocessable Entity`.

## Contrato da API

`POST /route` aceita um schema fechado: campos desconhecidos são rejeitados, identificadores devem
ser strings não vazias, timestamps devem ser valores UTC com timezone e os limites numéricos devem
ser positivos.

| Campo | Valores aceitos ou regra |
|---|---|
| `schema_version` | Exatamente `1.0` |
| `requested_at` | Timestamp UTC |
| `workflow_id`, `task_id`, `agent_name` | Strings não vazias |
| `workload` | `document_extraction`, `cashflow_analysis`, `findings_correlation`, `opinion_drafting` ou `json_repair` |
| `risk_level` | `low`, `medium`, `high` ou `critical` |
| `data_classification` | `public`, `internal`, `confidential` ou `restricted` |
| `context_tokens_estimated` | Inteiro maior ou igual a zero |
| `structured_output_required` | Booleano |
| `max_latency_ms` | Inteiro positivo |
| `max_cost_usd` | Valor decimal positivo |

Os códigos de erro estáveis são:

| Status HTTP | Código | Significado |
|---:|---|---|
| 401 | `unauthorized` | Header `X-API-Key` ausente ou inválido |
| 422 | `invalid_request` | A requisição não corresponde ao contrato |
| 422 | `no_viable_model_group` | O grupo mapeado para a carga de trabalho falhou em uma restrição rígida |
| 429 | `rate_limit_exceeded` | Excesso de requisições para o par `(IP do cliente, agent_name)` |
| 500 | `misconfigured_routing_policy` | A política em execução não tem mapeamento para uma carga de trabalho reconhecida |

Uma política YAML ausente, malformada, com campos desconhecidos ou incompleta impede o serviço de
iniciar.

## Configuração da política

Edite [`config/routing_policy.yaml`](config/routing_policy.yaml) para gerenciar os mapeamentos de
carga de trabalho e as capacidades dos grupos de modelo. O carregador exige cobertura completa de
toda carga de trabalho e grupo de modelo declarado, e rejeita campos desconhecidos.

Use `ROUTING_POLICY_PATH` para carregar um arquivo específico de ambiente:

```bash
ROUTING_POLICY_PATH=/etc/policy-model-router/routing_policy.yaml \
  uv run uvicorn policy_model_router.entrypoints.http:app --host 0.0.0.0 --port 8000
```

Outras configurações de runtime:

| Variável de ambiente | Padrão | Finalidade |
|---|---|---|
| `APP_ENV` | `development` | Rótulo de ambiente anexado aos logs estruturados |
| `LOG_LEVEL` | `INFO` | Nível de logging do Python |
| `LOG_FORMAT` | `json` | Use `console` para logs locais legíveis por humanos |
| `API_KEYS` | *(obrigatória)* | Objeto JSON mapeando cada `agent_name` à sua própria chave, comparada ao header `X-API-Key` em `POST /route`; o serviço recusa iniciar se estiver ausente, vazia ou malformada |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Requisições permitidas por par `(IP do cliente, agent_name)` por janela |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Duração da janela de rate limit, em segundos |
| `REDIS_URL` | *(ausente)* | Opcional. Compartilha o rate limit entre réplicas via Redis (ADR-0008); requer `uv sync --extra rate-limit`. Se ausente, mantém o limitador padrão em memória, por processo |

## Autenticação e rate limiting

`POST /route` exige um header `X-API-Key` válido, comparado (em tempo constante) contra a chave
configurada para o próprio `agent_name` da requisição em `API_KEYS`. Uma chave ausente, incorreta,
ou pertencente a outro agente sempre retorna `401 unauthorized` — a resposta nunca revela quais
agentes estão configurados. A chave de um agente pode ser rotacionada ou revogada sem afetar os
demais. Isso ainda não é um IAM completo: não há expiração de chave, escopo além de "pode chamar
`/route` como este agente", nem garantia de identidade além de "sabia a chave certa" — veja a
[emenda da ADR-0007](docs/adr/0007-http-boundary-hardening.md) para o que um mecanismo mais forte
(mTLS, OAuth2 client credentials) acrescentaria.

Também há rate limiting por par `(IP do cliente, agent_name)` (`RATE_LIMIT_MAX_REQUESTS` por
`RATE_LIMIT_WINDOW_SECONDS`); ultrapassar o limite retorna `429 rate_limit_exceeded`. Por padrão é
um contador em memória, de janela fixa, **por processo** — um deployment com múltiplas instâncias
aplica o limite por instância, não de forma global no cluster (ADR-0007). Defina `REDIS_URL` (e
instale `uv sync --extra rate-limit`) para compartilhar o limite entre réplicas; use
`docker compose up -d redis` para uma instância local. O limitador com Redis falha aberto em caso
de erro no backend (permite a requisição em vez de bloquear o tráfego de roteamento por uma
indisponibilidade não relacionada), mas falha fechado na inicialização se o Redis configurado
estiver inacessível (ADR-0008).

## Disponibilidade

`ModelGroupProfile.available` em `config/routing_policy.yaml` é um flag estático, editado à mão. A
camada de aplicação resolve esse valor através de um port `AvailabilityProvider`
([ADR-0006](docs/adr/0006-availability-provider-port.md)); a única implementação incluída hoje,
`StaticAvailabilityProvider`, apenas repassa esse flag sem alteração. Ainda não há verificação de
saúde em tempo real de provedor/gateway — o port existe para que um adapter real possa ser
adicionado depois, sem alterar o caso de uso de roteamento nem as restrições de domínio.

## Saúde, prontidão e métricas

`GET /health` sempre retorna `200 {"status": "ok"}` assim que o processo está servindo requisições.
`GET /readyz` retorna `200 {"status": "ready"}` assim que a política de roteamento é carregada com
sucesso na inicialização. `GET /metrics` retorna saída em formato Prometheus, hoje limitada a
`policy_model_router_rate_limiter_backend_unavailable_total` — um contador incrementado toda vez
que o rate limiter com Redis falha aberto porque o Redis estava inacessível; monitore
`increase(policy_model_router_rate_limiter_backend_unavailable_total[5m]) > 0` (somado entre
réplicas) para detectar uma indisponibilidade prolongada em vez de depender só da linha de log
`rate_limiter_backend_unavailable`. Nenhum dos três endpoints exige `X-API-Key` nem conta para o
rate limit, então orquestradores e scrapers podem monitorá-los sem custo. `/readyz` é uma checagem
rasa: este serviço não tem dependência externa para verificar, então "pronto" significa
"inicialização concluída", não "um sistema posterior está saudável".

## Container

Construa e execute a imagem multi-stage e non-root:

```bash
docker build -t policy-model-router .
docker run --rm -p 8000:8000 policy-model-router
```

Tags SemVer disparam o workflow de publicação do repositório, que constrói a imagem e envia suas
tags versionadas para o GitHub Container Registry após o quality gate passar.

## Arquitetura

O código segue uma direção de dependência de Clean Architecture:

```text
entrypoints -> application -> domain
adapters    -> application/domain
domain      -> no outer layer
```

- `domain`: vocabulários fechados, objetos de valor de política, requisições e decisões de
  roteamento, e predicados de restrição puros;
- `application`: caso de uso de roteamento determinístico e ports de clock/ID/disponibilidade;
- `adapters`: carregador de política YAML, clock do sistema, gerador de UUID, provedor estático de
  disponibilidade, rate limiter em memória (padrão) e rate limiter opcional com Redis, e suporte a
  tracing opcional;
- `entrypoints`: contratos Pydantic de wire, endpoints FastAPI (`/route`, `/health`, `/readyz`),
  mapeamento de erros e logging estruturado.

A política é carregada uma única vez na inicialização, e o tratamento de requisições é *stateless*.
Veja [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) para as regras de dependência e diagramas, e o
[índice de ADRs](docs/ARCHITECTURE.md#related-decisions) para entender por que a fronteira de
provedor ([ADR-0004](docs/adr/0004-litellm-provider-boundary.md)), o algoritmo de roteamento
([ADR-0005](docs/adr/0005-deterministic-policy-routing.md)), o seam de disponibilidade
([ADR-0006](docs/adr/0006-availability-provider-port.md)), o endurecimento da fronteira HTTP
([ADR-0007](docs/adr/0007-http-boundary-hardening.md)) e o rate limiter compartilhado opcional
([ADR-0008](docs/adr/0008-redis-shared-rate-limiter.md)) são como são.

## Escopo atual

O MVP intencionalmente não:

- escolhe um provedor, deployment ou credencial de API;
- chama um modelo nem executa uma verificação de saúde em tempo real contra um provedor/gateway; a
  disponibilidade é resolvida através do port `AvailabilityProvider`, mas a única implementação
  incluída ainda repassa o flag estático do YAML (veja [Disponibilidade](#disponibilidade));
- pontua ou ranqueia alternativas viáveis;
- aplica fallback quando o grupo mapeado para a carga de trabalho é rejeitado;
- fornece um IAM completo: `API_KEYS` por agente autentica um `agent_name` reivindicado, mas sem
  expiração, escopo ou garantia de identidade além de "sabia a chave certa" (veja
  [Autenticação e rate limiting](#autenticação-e-rate-limiting));
- compartilha o estado de rate limit entre réplicas *por padrão*; isso exige habilitar `REDIS_URL`,
  o que por sua vez adiciona o Redis como uma dependência de infraestrutura real, com sua própria
  disponibilidade a gerenciar.

Essas fronteiras mantêm as decisões de política explícitas. Adicione fallback, pontuação,
verificação de saúde em tempo real ou garantias mais fortes de identidade/alta disponibilidade
somente quando houver dados de avaliação ou um requisito concreto de deployment que justifique o
comportamento. Veja [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#known-gaps) para a lista completa
de débitos rastreados.

## Desenvolvimento

Execute a suíte de testes:

```bash
uv run pytest
```

Execute formatação, lint, checagem de tipos, testes, checagens de segurança, auditoria de
dependências, checagens de arquitetura e empacotamento através do gate do projeto:

```bash
uv run python scripts/quality_gate.py
```

Liste as checagens disponíveis ou execute uma isoladamente:

```bash
uv run python scripts/quality_gate.py --list
uv run python scripts/quality_gate.py --check tests
```

Orientações de engenharia adicionais estão disponíveis em [`AGENTS.md`](AGENTS.md) e
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).
