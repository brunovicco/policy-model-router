# Policy Model Router

[English](README.md) | **Português (Brasil)**

[![quality](https://github.com/brunovicco/policy-model-router/actions/workflows/quality.yml/badge.svg)](https://github.com/brunovicco/policy-model-router/actions/workflows/quality.yml)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![ghcr.io](https://img.shields.io/badge/ghcr.io-policy--model--router-2496ED?logo=docker&logoColor=white)](https://github.com/brunovicco/policy-model-router/pkgs/container/policy-model-router)

> Um ponto de decisão de política *fail-closed* que decide **qual classe de modelo pode atender uma
> carga de trabalho de LLM** — antes da inferência, e fora dos prompts dos agentes e do código de
> aplicação.

O chamador declara uma carga de trabalho, seu nível de risco, sua classificação de dados e seus
limites operacionais. `POST /route` avalia isso contra uma política versionada e responde com um
registro de decisão explicável — ou uma rejeição explícita carregando a mesma proveniência. Ele
nunca chama um modelo, nunca vê um prompt e não guarda credencial de provedor.

```text
Agente / aplicação
        │
        │ carga de trabalho + risco + classificação de dados + limites
        ▼
Policy Model Router  (PDP)  ← este repositório
        ├─ validação de contrato fechado
        ├─ autorização assinada da Governança + parada de emergência   (opcional)
        ├─ consulta carga de trabalho → grupo de modelo
        ├─ restrições eliminatórias ordenadas
        └─ registro de decisão com proveniência de política e deployment
        │
        │ selected_model_group, e por que nenhum outro grupo foi
        ▼
Governed LLM Gateway  (PEP)
        ▼
Provedores de LLM
```

```text
conjunto permitido pelo Gateway ⊆ conjunto autorizado pelo Policy Router
```

O ponto de aplicação pode estreitar o que este roteador autorizou. Nunca pode ampliar.

[Uma decisão governada](#uma-decisão-governada) · [O que você ganha](#o-que-você-ganha) · [Como funciona](#como-funciona) · [Executando](#executando) · [O arquivo de política](#o-arquivo-de-política) · [Contrato da API](#contrato-da-api) · [Deployments governados](#deployments-governados) · [Observabilidade](#observabilidade) · [Mapa do repositório](#mapa-do-repositório) · [Escopo](#escopo) · [Onde ler a seguir](#onde-ler-a-seguir)

## Uma decisão governada

Dados restritos e contexto de 100.000 tokens. Só o grupo mapeado pela carga de trabalho sobrevive,
e a resposta diz exatamente por que cada um dos outros não sobreviveu.

```bash
curl -X POST http://127.0.0.1:8000/route \
  -H 'Content-Type: application/json' -H 'X-API-Key: dev-local-key' \
  -d '{
    "schema_version": "1.0", "requested_at": "2026-07-22T12:00:00Z",
    "workflow_id": "credit-review-42", "task_id": "correlate-findings-7",
    "agent_name": "credit-analysis-agent", "workload": "findings_correlation",
    "risk_level": "high", "data_classification": "restricted",
    "context_tokens_estimated": 100000, "max_output_tokens_estimated": 2000,
    "structured_output_required": false, "max_latency_ms": 60000, "max_cost_usd": 1.00
  }'
```

```json
{
  "schema_version": "1.0",
  "routing_decision_id": "674088f4-cd75-45e9-a6b5-5e85b8cc5588",
  "decided_at": "2026-07-22T12:00:01Z",
  "selected_model_group": "reasoning-strong",
  "reason": "workload 'findings_correlation' maps to model group 'reasoning-strong' and satisfies all constraints",
  "rejected_candidates": [
    {
      "model_group": "fast-small",
      "reason_code": "data_classification_not_authorized",
      "observed_value": "restricted",
      "required_value": "public, internal"
    },
    {
      "model_group": "reasoning-medium",
      "reason_code": "context_window_exceeded",
      "observed_value": "102000",
      "required_value": "<= 64000"
    }
  ],
  "policy_id": "credit-desk-routing",
  "policy_version": "1.0.0",
  "policy_digest": "sha256:2f1a...c9",
  "service_version": "0.5.0",
  "environment": "production"
}
```

Toda rejeição carrega um `reason_code` legível por máquina com o `observed_value` e o
`required_value`, então uma trilha de auditoria ou uma UI nunca precisa parsear prosa. O
`policy_digest` é um SHA-256 do conteúdo da política carregada, então uma decisão nomeia a política
exata que a produziu mesmo quando ninguém lembrou de subir o `policy_version`.

**Uma negação é tão auditável quanto uma aprovação.** Quando o grupo mapeado falha uma restrição, o
roteador responde `422` e não promove a requisição silenciosamente para um grupo mais forte — e essa
rejeição carrega os mesmos cinco campos de proveniência que uma aceitação. O ponto de aplicação
adiante precisa conseguir provar *qual* política negou uma chamada, não apenas que algo negou.

## O que você ganha

| Capacidade | O que significa na prática |
| --- | --- |
| **Escolha de modelo fora dos prompts** | Um agente declara o que precisa, não qual modelo quer. Mudar o mapeamento é editar política, não editar prompt em cada agente. |
| **Determinístico e reproduzível** | A mesma requisição contra a mesma política dá o mesmo grupo e as mesmas razões de rejeição. Sem pontuação, sem amostragem, sem desempate. |
| **Explicável por construção** | Todo grupo não selecionado aparece na decisão com a restrição que o eliminou. |
| **Fail-closed em toda parte** | Política inválida, carga de trabalho desconhecida, snapshot de controle ausente, assinatura não verificável: cada um nega em vez de degradar. |
| **Proveniência em todo desfecho** | Id, versão e digest da política, mais versão do serviço e ambiente — em rejeições tanto quanto em decisões. |
| **Escopo de runtime governado** | Opcionalmente exige uma autorização Ed25519 assinada, de uso único, vinculada à requisição exata, mais uma parada de emergência. |

## Como funciona

Para cada requisição, o caso de uso busca o grupo de modelo que a carga de trabalho mapeia, avalia
**todos** os grupos declarados contra as restrições ordenadas abaixo — parando na primeira falha de
cada candidato — e seleciona o grupo mapeado só se ele sobreviveu. Todo outro grupo é reportado como
rejeitado, seja pela restrição que falhou, seja porque a carga mapeia para outro lugar.

Avaliar os grupos que não podem ser selecionados é deliberado: é o que torna a decisão explicável. É
custo de auditoria, não custo de roteamento.

A ordem importa, porque a primeira restrição que um candidato falha vira sua razão de rejeição.

| # | Restrição | O candidato é rejeitado quando |
|---:|---|---|
| 1 | Classificação de dados | O grupo não é autorizado para a classificação da requisição |
| 2 | Nível de risco | O grupo não é autorizado para o nível de risco do fluxo |
| 3 | Saída estruturada | A requisição exige e o grupo não suporta |
| 4 | Chamada de ferramentas | A carga de trabalho exige e o grupo não suporta |
| 5 | Janela de contexto | Entrada estimada + saída esperada juntas excedem o limite do grupo |
| 6 | Teto de custo | O custo estimado por token excede `max_cost_usd` |
| 7 | Teto de latência | A latência típica do grupo excede `max_latency_ms` |
| 8 | Disponibilidade | O provedor de disponibilidade resolve o grupo como indisponível |
| 9 | Allowlist de agente | O grupo é restrito e o agente solicitante não está listado |

Os predicados são funções puras em [`domain/constraints.py`](src/policy_model_router/domain/constraints.py);
o algoritmo de dois passos está em [`application/route_model.py`](src/policy_model_router/application/route_model.py).

## Executando

Requisitos: Python 3.13 e [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brunovicco/policy-model-router.git
cd policy-model-router
uv sync --frozen
export API_KEYS='{"credit-analysis-agent":"dev-local-key"}'   # obrigatório, chaveado por agent_name
uv run uvicorn policy_model_router.entrypoints.http:app --reload
```

O serviço escuta em `http://127.0.0.1:8000` e carrega `config/routing_policy.yaml` na inicialização.

### Container

```bash
docker run --rm -p 8000:8000 \
  -e API_KEYS='{"credit-analysis-agent":"dev-local-key"}' \
  ghcr.io/brunovicco/policy-model-router:0.5
```

A imagem é não-root e multi-stage, declara um `HEALTHCHECK` contra `/health` e respeita
`TRUSTED_PROXY_IPS` — sem valor por padrão, então a chave de rate limit usa o endereço TCP real do
par e nenhum cabeçalho encaminhado é confiado. Defina com o endereço do próprio proxy atrás de um
ingress; nunca com `*`, ou qualquer cliente forja o cabeçalho e multiplica a própria cota.

## O arquivo de política

[`config/routing_policy.yaml`](config/routing_policy.yaml) guarda os mapeamentos de carga de
trabalho e as capacidades dos grupos de modelo. O carregador exige cobertura completa, rejeita
campos desconhecidos e se recusa a subir com qualquer coisa malformada. Ele também rejeita um grupo
que nenhuma carga mapeia, para que configuração não fique para trás por descuido — declare
`staged: true` num canary ou numa reserva que você provisiona de propósito antes da carga dele.

O exemplo incluído declara cinco cargas de trabalho em quatro grupos lógicos. Os valores são
entradas de política mantidas pelo autor da política, não medições ao vivo de provedor.

| Grupo de modelo | Dados autorizados | Risco autorizado | Contexto | Latência típica | USD / M tokens (in / out) |
|---|---|---|---:|---:|---:|
| `fast-small` | public, internal | low, medium | 16k | 3.000 ms | 0,10 / 0,40 |
| `reasoning-medium` | + confidential, restricted | + high | 64k | 15.000 ms | 0,50 / 1,50 |
| `reasoning-strong` | + confidential, restricted | + critical | 128k | 30.000 ms | 2,00 / 8,00 |
| `fast-structured-output` | public, internal | low, medium | 8k | 2.000 ms | 0,10 / 0,40 |

Risco autorizado é uma regra de qualidade de decisão, não de proteção de dados: um grupo pode estar
liberado para os dados envolvidos e ainda assim não ser autorizado para uma decisão de alto risco.

**Recarga.** Envie `SIGHUP` para reler a política sem reiniciar:

```bash
docker kill --signal=HUP <container>      # ou: kubectl exec <pod> -- kill -HUP 1
```

A troca é atômica — uma requisição resolve a política uma vez e a mantém até terminar, então o
`policy_digest` que uma decisão reporta é o que de fato decidiu. Se o arquivo novo falhar ao
carregar, a política em uso é mantida e o serviço continua atendendo: recusar transformaria um erro
de digitação no YAML em indisponibilidade. Monitore
`policy_model_router_policy_reloads_total{outcome="failed"}` em vez de supor que a recarga
funcionou. Com múltiplos workers, cada um tem a própria política e precisa do próprio sinal.

## Contrato da API

`POST /route` aceita um schema fechado: campos desconhecidos são rejeitados, timestamps precisam ser
UTC com fuso explícito, e limites numéricos precisam ser positivos.

| Campo | Valores aceitos |
|---|---|
| `schema_version` | Exatamente `1.0` |
| `requested_at` | Timestamp UTC |
| `workflow_id`, `task_id`, `agent_name` | Não vazios, no máximo 200 caracteres |
| `workload` | Qualquer identificador definido por política: 1–128 caracteres minúsculos de `a-z0-9._-`, alfanumérico nas duas pontas. Novas cargas são qualificadas por namespace (`rag.answer`). Um identificador válido que a política não declara não é rejeitado pelo schema — ele chega à fronteira de política e falha fechado ali ([ADR-0015](docs/adr/0015-policy-defined-workload-and-model-group-identifiers.md), [guia de migração](docs/MIGRATION_TO_GENERIC_POLICY.md)) |
| `risk_level` | `low`, `medium`, `high`, `critical` |
| `data_classification` | `public`, `internal`, `confidential`, `restricted` |
| `context_tokens_estimated`, `max_output_tokens_estimated` | Inteiro, 0 a 10.000.000 |
| `structured_output_required` | Booleano |
| `max_latency_ms` | Inteiro positivo |
| `max_cost_usd` | Decimal positivo |

As duas estimativas de token alimentam a restrição de custo: um grupo é precificado por token,
entrada e saída separadamente, então o custo estimado é função do tamanho real da chamada
([ADR-0010](docs/adr/0010-token-based-cost-estimation.md)).

| Status | Código | Significado |
|---:|---|---|
| 401 | `unauthorized` | `X-API-Key` ausente ou inválida |
| 403 | *(código de negação de runtime, limitado)* | Autorização ou controle de runtime negou; o corpo traz um envelope `violation` |
| 413 | `payload_too_large` | Corpo excede `MAX_REQUEST_BODY_BYTES` pelo `Content-Length` declarado |
| 422 | `invalid_request` | A requisição não bate com o contrato |
| 422 | `no_viable_model_group` | O grupo mapeado falhou uma restrição rígida; o corpo traz a decisão rejeitada completa |
| 429 | `rate_limit_exceeded` | Requisições demais para este par `(IP do cliente, agent_name)` |
| 500 | `misconfigured_routing_policy` | A política ativa não declara regra para a carga de trabalho solicitada |

**Autenticação** é uma chave de API por agente, comparada em tempo constante com a chave configurada
para o `agent_name` da própria requisição. Um agente desconhecido e uma chave errada retornam o
mesmo erro, então a resposta nunca revela quais agentes estão configurados. Isso não é um IAM
completo — sem expiração, sem escopo, sem garantia além de "sabia a chave certa"
([ADR-0007](docs/adr/0007-http-boundary-hardening.md)).

**Rate limiting** roda em duas camadas, ambas *antes* da autenticação para que tentativas com chave
inválida também sejam limitadas: uma camada por IP em middleware ASGI, antes do parsing do corpo, e
depois uma por `(IP, agent_name)`. A primeira existe para que um chamador não escape da segunda
variando o `agent_name`. Ambas são por processo por padrão; defina `REDIS_URL` para compartilhá-las
entre réplicas ([ADR-0008](docs/adr/0008-redis-shared-rate-limiter.md)).

As configurações estão em [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Deployments governados

Tudo acima é a fronteira de política do próprio roteador. Um deployment governado pode exigir,
adicionalmente, que cada requisição chegue dentro de um **escopo de runtime assinado** emitido por
uma autoridade de Governança externa, e que uma parada de emergência seja respeitada antes de
qualquer decisão. Os dois estão desligados por padrão e são obrigatórios em `staging`/`production` —
esses ambientes se recusam a subir sem eles.

Com a aplicação ligada, `POST /route` recebe `{"request": ..., "authorization": ...}`, e o envelope é
verificado em ordem fixa antes do roteamento e conferido de novo depois:

1. **Identidade e tempo** — emissor, audiência, janela de validade, limitada a dez minutos.
2. **Chave** — resolvida por `kid` exato contra um conjunto público de confiança, sem fallback.
3. **Assinatura** — Ed25519 sobre JSON canônico, compatível byte a byte com o repositório emissor.
4. **Vínculo com a requisição** — onze fatos precisam bater com os claims assinados.
5. **Vínculo com o agente** — o `agent_name` precisa mapear para o `agent_id` assinado.
6. **Proveniência de política** — a identidade de política e catálogo assinada precisa ser a confiada.
7. **Controle de runtime** — parada de emergência e piso de revogação, lidos de uma projeção.
8. **Uso único** — o identificador da autorização é consumido atomicamente; repetição é negada.
9. **Modelo selecionado** — *depois* do roteamento, o grupo selecionado precisa estar no escopo assinado.

Cada etapa falha fechado com um código limitado, e uma negação retorna `403` com um envelope
`violation` de conteúdo minimizado e vinculado por digest: categoria, código, identificadores
estruturais e nada mais. Nenhum prompt, cabeçalho, credencial ou conteúdo de requisição, por
construção.

A configuração completa, o formato do envelope, todos os códigos de negação e as dezenove
configurações estão em
[`docs/runtime-authorization-operations.md`](docs/runtime-authorization-operations.md).

## Observabilidade

`GET /health` e `GET /readyz` são sondas de liveness e de inicialização; `GET /metrics` é texto
Prometheus. Os três são não autenticados e não limitados por design, então restrinja-os no ingress.

| Métrica | Labels |
|---|---|
| `policy_model_router_route_decisions_total` | `workload`, `model_group` |
| `policy_model_router_route_rejections_total` | `workload`, `outcome` |
| `policy_model_router_route_duration_seconds` | `workload` |
| `policy_model_router_rate_limit_decisions_total` | `tier`, `outcome` |
| `policy_model_router_rate_limiter_backend_unavailable_total` | — |
| `policy_model_router_runtime_authorization_total` | `outcome` |
| `policy_model_router_runtime_violations_total` | `category`, `code` |
| `policy_model_router_policy_reloads_total` | `outcome` |

O label `workload` carrega a carga solicitada apenas quando a política ativa a declara; qualquer
outra é reportada como `undeclared`, já que cargas são identificadores fornecidos pelo chamador e um
label ilimitado é memória ilimitada. Os logs estruturados guardam o valor literal.

Toda decisão também emite um log `routing_decision` com id da decisão, id de correlação, carga de
trabalho, grupo de modelo, código de razão e identidade da política. `workflow_id` e `task_id`
fornecidos pelo chamador ficam fora dos logs, conforme [`docs/PRIVACY.md`](docs/PRIVACY.md). O
contexto de trace W3C recebido é continuado através da fronteira.

## Mapa do repositório

| Caminho | O que vive ali |
|---|---|
| `src/policy_model_router/domain/` | Vocabulários controlados, objetos de valor de política, predicados puros, conjunto de chaves confiáveis, estado da parada de emergência |
| `src/policy_model_router/application/` | O caso de uso de roteamento, o verificador de autorização, o enforcer de controle e seus ports |
| `src/policy_model_router/adapters/` | Carregador YAML, clock, IDs, disponibilidade, rate limiters, guards antirrepetição, stores de projeção |
| `src/policy_model_router/entrypoints/` | Contratos Pydantic de wire, app FastAPI, configurações, mapeamento de erros, evidência de violação |
| `config/`, `examples/policies/` | A política incluída e políticas de exemplo alternativas |
| `docs/adr/` | Quinze decisões aceitas, emendadas em vez de reescritas |
| `scripts/` | O gate de qualidade do projeto e seus validadores de arquitetura, MCP e contratos |

As dependências apontam só para dentro — `entrypoints → application → domain`,
`adapters → application/domain`, e `domain` não depende de camada externa. O gate falha em qualquer
módulo fora de uma camada, então a regra é aplicada, não apenas documentada.

## Escopo

Este serviço deliberadamente não:

- escolhe provedor, deployment ou credencial, nem chama um modelo;
- executa verificação de saúde em tempo real — a disponibilidade é resolvida por um port, mas a
  única implementação incluída repassa o flag estático da política;
- pontua ou ranqueia alternativas viáveis, nem aplica fallback quando o grupo mapeado é rejeitado;
- fornece IAM completo na borda de transporte;
- observa o arquivo de política nem o recarrega sozinho;
- compartilha estado de rate limit entre réplicas sem habilitar o Redis.

Essas fronteiras mantêm as decisões de política explícitas. Os débitos rastreados estão em
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#known-gaps).

## Onde ler a seguir

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — camadas, regras de dependência, diagramas, débitos
- [`docs/adr/`](docs/adr/) — por que cada fronteira é como é
- [`docs/runtime-authorization-operations.md`](docs/runtime-authorization-operations.md) — deployments governados, ponta a ponta
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — todas as variáveis de ambiente
- [`docs/MIGRATION_TO_GENERIC_POLICY.md`](docs/MIGRATION_TO_GENERIC_POLICY.md) — identificadores definidos por política
- [`docs/PRIVACY.md`](docs/PRIVACY.md) — o que nunca chega a um log
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) e [`AGENTS.md`](AGENTS.md) — trabalhando neste repositório

```bash
uv run python scripts/quality_gate.py        # lint, format, tipos, testes, segurança, auditoria, empacotamento
```

## Licença

[MIT](LICENSE).
