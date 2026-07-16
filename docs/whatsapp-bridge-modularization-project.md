# Projeto de modularizacao completa do WhatsApp Bridge

Status: pronto para implementacao

Data-base: 2026-07-16

Branch-base: `github-update-20260603`

Commit-base: `71dd3a6`

Versao-base: `v1.0.99`

## 1. Objetivo

Transformar `backend/services/whatsapp_bridge.py` em uma fachada de compatibilidade,
distribuindo orquestracao, aprovacoes, runtime, persistencia e endpoints em modulos
coesos sob `backend/services/whatsapp/`.

O projeto deve reduzir o bridge de 10.946 para no maximo 1.800 linhas, sem alterar:

- as 14 rotas HTTP;
- os 8 schemas OpenAPI;
- os 24 nomes de `whatsapp_bridge.__all__`;
- os formatos de `whatsapp_bridge.json`, `whatsapp_bridge_state.json` e
  `whatsapp_bridge.db`;
- a ordem de processamento por telefone;
- a politica de aprovacao e de somente leitura;
- os contratos do gateway Cloudflare;
- o comportamento externo de Luna, Sol e Function Manager;
- a recuperacao de jobs depois de reinicio.

## 2. Diagnostico atual

### 2.1 Metricas

| Indicador | Estado atual | Meta final |
|---|---:|---:|
| Linhas em `whatsapp_bridge.py` | 10.946 | <= 1.800 |
| Funcoes de nivel superior no bridge | 321 | <= 90 delegadores/aliases explicitos |
| Funcoes com 50 linhas ou mais | 59 | 0 sem justificativa |
| Funcoes com 100 linhas ou mais | 27 | 0 |
| Maior funcao | 442 linhas | <= 120 linhas |
| Maior modulo novo de dominio | 441 linhas | <= 900 linhas |
| Arquivos no pacote WhatsApp | 11 | aproximadamente 37 |

As maiores concentracoes atuais sao:

- `_process_dual_codex_message`: 442 linhas;
- `_process_message`: 338 linhas;
- `_handle_question_natural_language`: 249 linhas;
- `_complete_dual_job_group_pending`: 223 linhas;
- `_handle_question_approval_command`: 218 linhas;
- `_function_manager_job`: 216 linhas;
- `_public_status`: 215 linhas;
- `whatsapp_bridge_update_config`: 209 linhas.

### 2.2 Causas da concentracao

O bridge mistura cinco tipos de responsabilidade:

1. regras de negocio e montagem de respostas;
2. integracao com gateway, Codex, ferramentas, voz e transcricao;
3. persistencia de configuracao, estado, pendencias e historico;
4. concorrencia: locks, pools, filas, futures e threads;
5. composicao HTTP e compatibilidade legada.

O risco principal nao e o tamanho isolado. Funcoes internas chamam outras funcoes
do proprio modulo e os testes substituem muitos desses nomes por monkeypatch. Uma
extracao direta poderia ignorar o monkeypatch, duplicar envios ou quebrar a ordem por
telefone.

## 3. Principios obrigatorios

1. Nenhum modulo em `backend/services/whatsapp/` pode importar
   `backend.services.whatsapp_bridge`.
2. A dependencia sempre aponta das camadas externas para as internas.
3. Relogio, persistencia, gateway, criacao de tarefas e callbacks variaveis sao
   recebidos por parametro ou por um objeto de dependencias.
4. Nomes usados pelos testes como pontos de monkeypatch sao resolvidos na fachada no
   momento da chamada, e nao capturados durante o import.
5. Estado concorrente possui um unico dono; outros modulos recebem referencias ou
   operacoes atomicas, nunca copias.
6. Cada commit deve deixar todas as suites relacionadas verdes.
7. Nao havera mudanca de schema de persistencia durante a modularizacao.
8. Nao havera nova funcionalidade, alteracao de prompt ou troca de modelo misturada
   aos commits de refatoracao.
9. Correcoes de defeitos exigem teste que falhe antes da correcao e commit `fix:`
   separado.
10. Alteracoes paralelas de `shared_sync` e scripts de SKU ficam fora de todos os
    commits deste projeto.

## 4. Arquitetura-alvo

```text
backend/services/
|-- whatsapp_bridge.py                 # fachada e composicao
|-- whatsapp_bridge_store.py           # persistencia existente
`-- whatsapp/
    |-- __init__.py                     # somente os 8 contratos publicos atuais
    |-- contracts.py
    |-- formatting.py
    |-- gateway.py
    |-- intent.py
    |-- media.py
    |-- message.py
    |-- report_scheduling.py
    |-- retry_policy.py
    |-- settings.py
    |-- tool_results.py
    |-- composition.py                  # protocolos e dependencias injetadas
    |-- config_store.py                 # defaults, load/save e caminhos
    |-- query_context.py                # loja, paginacao e contexto persistido
    |-- delivery.py                     # typing, progress e envio ao gateway
    |-- artifacts.py                    # imagens, documentos e relatorios
    |-- transcription.py                # Whisper e download de midia/audio
    |-- approvals/
    |   |-- __init__.py
    |   |-- question_tokens.py
    |   |-- question_workflow.py
    |   `-- actions.py
    |-- orchestration/
    |   |-- __init__.py
    |   |-- conversation.py             # Luna e contexto conversacional
    |   |-- function_manager.py
    |   |-- manager_results.py
    |   |-- retry_coordinator.py
    |   |-- pending.py
    |   |-- completion.py
    |   `-- processor.py                # selecao de IA e fluxo da mensagem
    |-- runtime/
    |   |-- __init__.py
    |   |-- state.py
    |   |-- dispatcher.py
    |   |-- monitor.py
    |   |-- scheduler.py
    |   `-- lifecycle.py
    |-- api_status.py
    `-- api_endpoints.py
```

O numero exato pode variar durante a implementacao, mas a divisao de responsabilidade
e os limites de tamanho nao podem ser relaxados sem justificativa no commit.

## 5. Direcao das dependencias

```text
contracts / settings / intent / media / message / retry_policy / tool_results
                                |
                                v
config_store / query_context / delivery / artifacts / transcription
                                |
                                v
approvals / orchestration
                                |
                                v
runtime
                                |
                                v
api_endpoints / whatsapp_bridge facade
```

- Modulos puros nao importam runtime, API nem fachada.
- Orquestracao pode usar adaptadores por interfaces, mas nao conhece FastAPI.
- Runtime controla threads e filas, mas nao implementa regra de negocio.
- Endpoints validam entrada e chamam servicos; nao manipulam locks diretamente.
- A fachada monta dependencias e preserva nomes legados.

## 6. Composicao e compatibilidade

### 6.1 Objeto de dependencias

Criar `BridgeDependencies` em `composition.py`, com callables para:

- relogio e geracao de IDs;
- leitura e gravacao de config/estado;
- consulta ao store SQLite;
- gateway e entregas;
- criacao e consulta de tarefas Codex;
- Luna, Sol e ferramentas do Function Manager;
- autorizacao, lojas e permissoes;
- voz, Whisper e artefatos;
- callbacks de conclusao, progresso e diagnostico.

A fachada deve construir essas dependencias por chamada ou por operacao de runtime.
Isso garante que `monkeypatch.setattr(whatsapp_bridge, "_save_state", ...)`, por
exemplo, continue sendo observado pelo componente extraido.

### 6.2 Estado de runtime

Criar `BridgeRuntimeState` em `runtime/state.py` para ser o unico proprietario de:

- stop events e threads;
- locks de bridge, configuracao, typing, progress, telefone e Function Manager;
- executores ativos e executores antigos;
- filas, futures, IDs em voo e sequencias;
- circuit breaker do fallback web;
- amostras de latencia e diagnosticos;
- caches de modelo e pulsos ativos.

Durante a migracao, a fachada mantem aliases para os dicionarios, conjuntos, locks e
constantes usados externamente. Para escalares substituidos diretamente nos testes,
os delegadores usam getters/setters injetados, evitando duas fontes de verdade.

### 6.3 Fachada final

`whatsapp_bridge.py` deve conter apenas:

- imports e aliases de compatibilidade;
- constantes publicadas anteriormente;
- instancia e composicao de `BridgeRuntimeState`;
- construcao de `BridgeDependencies`;
- delegadores necessarios para monkeypatch e assinaturas legadas;
- os 24 nomes de `__all__` na ordem atual.

## 7. Plano de execucao

O trabalho esta dividido em 10 fases, aproximadamente 25 commits incluindo o commit
final de empacotamento. Cada fase e um checkpoint recuperavel;
nenhuma fase depende de manter o branch quebrado entre commits.

### Fase 0 - Linha de base e caracterizacao

Entregas:

- congelar contagem de rotas, schemas, `__all__` e assinaturas;
- criar inventario dos nomes monkeypatchados e globais acessados pelos testes;
- registrar hashes dos schemas OpenAPI do router WhatsApp;
- criar teste de formatos de config, state e registros SQLite;
- criar teste de restart recovery com jobs legados;
- criar medidor AST para linhas, funcoes longas, ciclos e imports proibidos.

Commit: `test: characterize WhatsApp monolith compatibility surface`

Gate: suites WhatsApp completas.

### Fase 1 - Fundacao de composicao e estado

Entregas:

- adicionar `composition.py` e `runtime/state.py` sem mover comportamento;
- instanciar o estado na fachada;
- criar aliases compativeis para mutaveis globais;
- validar que monkeypatches continuam dinamicos;
- testar isolamento entre duas instancias de estado.

Commit: `refactor: introduce WhatsApp runtime composition`

Gate: concorrencia, resiliencia, dual agents e bridge.

### Fase 2 - Configuracao, persistencia e contexto de consulta

Entregas:

- extrair caminhos, defaults e load/save para `config_store.py`;
- extrair lojas autorizadas, selecao, heranca e memoria para `query_context.py`;
- manter `_load_config`, `_save_config`, `_load_state`, `_save_state` e todos os
  nomes `_whatsapp_*context*` como delegadores;
- preservar atomicidade de escrita e o monkeypatch de `datetime` e `_message_phone`.

Commits:

- `refactor: extract WhatsApp configuration store`
- `refactor: extract WhatsApp query context`

Gate: bridge, intent, message, config e store scope.

### Fase 3 - Entrega, artefatos e transcricao

Entregas:

- extrair typing, progress, resultados e proativos para `delivery.py`;
- extrair imagens, documentos e cards para `artifacts.py`;
- extrair Whisper, validacao do modelo, download de midia e transcricao para
  `transcription.py`;
- garantir idempotencia por `message_id` e nenhuma entrega duplicada.

Commits:

- `refactor: extract WhatsApp delivery runtime`
- `refactor: extract WhatsApp artifact delivery`
- `refactor: extract WhatsApp transcription workflow`

Gate: bridge, report visuals, voice, gateway e testes de falha de typing.

### Fase 4 - Aprovacoes

Entregas:

- extrair tokens, threads e localizacao de rascunho para `question_tokens.py`;
- extrair revisao natural, regeneracao e botoes para `question_workflow.py`;
- extrair aprovacao de mutacoes e store selection para `actions.py`;
- preservar TTL, uso unico, vinculo ao usuario e proibicao de aprovacao de tarefa
  pelo WhatsApp.

Commits:

- `refactor: extract WhatsApp question approval tokens`
- `refactor: extract WhatsApp question approval workflow`
- `refactor: extract WhatsApp action approvals`

Gate: bridge, aprovacoes, gateway interativo e Black Jhon.

### Fase 5 - Orquestracao dual e Function Manager

Entregas:

- mover historico, snapshots e Luna para `conversation.py`;
- mover catalogo, enforcement, execucao e diagnostico para `function_manager.py`;
- mover texto deterministico e evidencias para `manager_results.py`;
- mover classificacao operacional, restart recovery e reagendamento para
  `retry_coordinator.py`;
- decompor `_process_dual_codex_message` em funcoes de no maximo 120 linhas.

Commits:

- `refactor: extract WhatsApp conversation agent orchestration`
- `refactor: extract WhatsApp function manager`
- `refactor: extract WhatsApp deterministic manager results`
- `refactor: extract WhatsApp retry coordinator`

Gate: dual agents, deterministic outputs, resilience, tool results e consultas.

### Fase 6 - Pendencias, conclusao e processamento

Entregas:

- mover contrato, persistencia, archive e remocao de pendencias para `pending.py`;
- mover conclusao individual, agrupada, parcial e aprovacoes para `completion.py`;
- mover criacao de tarefa por provedor e fluxo principal para `processor.py`;
- decompor `_complete_dual_job_group_pending`, `_complete_dual_worker_pending`,
  `_complete_pending` e `_process_message` para o limite de 120 linhas;
- preservar o formato dos jobs e a recuperacao de todas as versoes existentes.

Commits:

- `refactor: extract WhatsApp pending state`
- `refactor: extract WhatsApp completion workflows`
- `refactor: extract WhatsApp message processor`

Gate: bridge, dual agents, approvals, restart recovery e provider selection.

### Fase 7 - Dispatcher, monitor, scheduler e lifecycle

Entregas:

- mover fila ordenada por telefone para `runtime/dispatcher.py`;
- mover expiracao, transicoes e acompanhamento para `runtime/monitor.py`;
- mover relatorios e alertas agendados para `runtime/scheduler.py`;
- mover polling, threads, start e stop para `runtime/lifecycle.py`;
- provar shutdown sem futures orfas e restart sem duplicacao.

Commits:

- `refactor: extract WhatsApp phone dispatcher`
- `refactor: extract WhatsApp pending monitor`
- `refactor: extract WhatsApp scheduled runtime`
- `refactor: extract WhatsApp lifecycle`

Gate: phone concurrency, resilience, scheduling, lifecycle e polling.

### Fase 8 - API, diagnosticos e fachada final

Entregas:

- mover status e diagnosticos para `api_status.py`;
- mover implementacao dos 14 handlers para `api_endpoints.py`;
- manter no bridge delegadores com assinaturas FastAPI identicas;
- remover imports e globais sem uso;
- fixar limite de 1.800 linhas e ausencia de funcao acima de 120 linhas;
- confirmar 14 rotas, 8 schemas e 24 exports.

Commits:

- `refactor: extract WhatsApp API status`
- `refactor: extract WhatsApp API endpoints`
- `refactor: reduce WhatsApp bridge to compatibility facade`

Gate: OpenAPI, router, config, voice, package e full suite.

### Fase 9 - Empacotamento e entrega

Entregas:

- adicionar todos os arquivos novos nas tres listas de recursos/paridade;
- elevar `minFiles` de `backend/services/whatsapp` de 11 para o total final;
- testar source, pacote preparado e pacote construido;
- confirmar que a copia instalada preserva dados e config em `info/`;
- enviar somente os arquivos deste projeto para o branch definido.

Commit: `build: package modular WhatsApp runtime`

Nenhuma tag, release, instalador publicado ou incremento de versao deve ocorrer sem
autorizacao separada.

## 8. Estrategia de testes

### 8.1 Testes novos

Criar testes especificos para:

- dependencia em camadas e ausencia de ciclos;
- nenhum componente importando a fachada;
- facade hooks respeitando monkeypatch depois do import;
- aliases de estado apontando para o mesmo objeto;
- isolamento entre runtimes;
- ordem por telefone e paralelismo entre telefones;
- shutdown com fila cheia e futures em execucao;
- restart no meio de Luna, Sol, Function Manager, aprovacao e retry;
- idempotencia de entrega, interativos, documentos e imagens;
- expiracao e uso unico de tokens;
- paridade de config/state/SQLite antes e depois;
- equivalencia componente versus fachada;
- orcamento de linhas e tamanho de funcoes;
- 14 rotas, 8 schemas, 24 exports e assinaturas;
- inventario e paridade de empacotamento.

### 8.2 Gates por nivel

1. Por commit: testes do componente e caracterizacao relacionada.
2. Por fase: todas as suites Python do WhatsApp.
3. Fases 5 a 8: regressao Python completa.
4. Final: Python, Node/Electron, gateway, package e pipeline completo.
5. Se houver instalacao autorizada: `/health`, OpenAPI, hashes de arquivos e um
   ciclo read-only real sem envio mutavel.

Baseline de aceite:

- no minimo 882 testes Python atuais, mais os novos;
- 20 regressões Node/Electron;
- 49 testes do gateway;
- pipeline completo 12/12;
- `verify:package` aprovado;
- zero regressao nas rotas e schemas.

## 9. Criterios de aceite

O projeto so esta concluido quando todos os itens abaixo forem verdadeiros:

- `whatsapp_bridge.py` possui no maximo 1.800 linhas;
- nenhum modulo WhatsApp possui mais de 900 linhas;
- nenhuma funcao possui mais de 120 linhas sem excecao documentada;
- nao existem ciclos entre os novos modulos;
- componentes nao importam a fachada;
- existe um unico proprietario para cada estado concorrente;
- formatos persistidos permanecem compativeis;
- polling, ordem por telefone, retries e restart recovery mantem comportamento;
- Luna, Sol e Function Manager mantem modelos, reasoning e contratos;
- aprovacoes continuam seguras e vinculadas ao mesmo usuario/conversa;
- rotas, schemas, `__all__` e assinaturas permanecem identicos;
- todas as suites e verificacoes de pacote passam;
- o diff e os commits nao incluem `shared_sync` nem scripts de SKU.

## 10. Riscos e mitigacoes

| Risco | Nivel | Mitigacao |
|---|---|---|
| Monkeypatch deixar de interceptar chamadas | Alto | Hooks resolvidos pela fachada em tempo de chamada |
| Duas fontes de verdade para globais | Alto | `BridgeRuntimeState` unico e aliases testados |
| Envio duplicado apos extracao | Alto | Chaves idempotentes e testes por `message_id` |
| Quebra de ordem por telefone | Alto | Dispatcher extraido sem mudar algoritmo e testes concorrentes |
| Job antigo nao recuperar | Alto | Fixtures versionadas e teste de restart antes da mudanca |
| Ciclo de imports | Medio | Regra de camadas e teste AST |
| Arquivo novo ausente no instalador | Alto | Paridade nas tres listas e `minFiles` final |
| Refatoracao misturar correcao funcional | Medio | Commits `fix:` separados com teste demonstrativo |
| Alteracoes paralelas entrarem no commit | Alto | `git add` apenas por caminhos deste projeto |

## 11. Rollback

- Cada dominio e extraido em commit independente.
- Nenhum commit altera schema persistido.
- A fachada permite reverter uma extracao sem alterar router ou consumidores.
- Em regressao funcional, reverter apenas o ultimo commit do dominio afetado.
- Nao usar `git reset --hard` ou descarte de alteracoes paralelas.
- Antes de eventual instalacao, criar snapshot dos arquivos de config/estado e do
  banco do bridge; nunca sobrescrever `info/` com o pacote.

## 12. Entregaveis finais

1. Fachada `whatsapp_bridge.py` com ate 1.800 linhas.
2. Pacote modular organizado conforme a arquitetura-alvo.
3. Testes de caracterizacao, unidade, integracao, concorrencia e empacotamento.
4. Medidor automatico de arquitetura e tamanho.
5. Manifesto do instalador atualizado.
6. Historico de commits por dominio, todos verdes.
7. Relatorio final com metricas antes/depois, testes, hashes e pendencias.
