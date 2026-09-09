# Correção do carregamento de Perguntas

## Entrega para revisão

Base: `242e61c5d9fbb7a0f6121237524ea4ce2b7f080b`, HEAD limpo do dev.
Branch de implementação: `codex/perguntas-loading-recovery-20260909`.
As alterações foram produzidas em worktree dedicado. A incorporação ao dev e à próxima versão depende da revisão e aprovação desta entrega.

Versão comercial preservada em `1.0.140`. Nenhum instalador, publicação, atualização instalada ou migração de dados operacionais foi realizado. As verificações usam dados fictícios e transporte simulado.

## Comportamento implementado

- Cada execução da interface pertence à sessão, loja, pergunta e geração correspondente. Retornar A → B → A recupera a consulta; respostas antigas não liberam nem sobrescrevem uma execução nova.
- Paginação prepara buffers, cursores, páginas e identificadores vistos em uma cópia temporária e os confirma juntos. A reprodução de cancelamento que entregava 71 de 80 perguntas passa a entregar as 80, sem duplicatas.
- Seleção, lista válida e rascunho permanecem durante falhas temporárias. A renovação do token na mesma identidade preserva a interação; troca de empresa, usuário ou sessão e revogações reais invalidam o escopo correspondente.
- Anúncio e histórico têm estados próprios e nova tentativa. Histórico consultado com sucesso, inclusive truncado nas 50 perguntas recentes do anúncio, permite IA com aviso. Nome do comprador é opcional; sua identidade precisa existir para filtrar o histórico corretamente.
- Atualizar renova lista, anúncios visíveis e detalhe selecionado. Tentativas automáticas se limitam aos componentes pendentes, a três execuções, com esperas de 2 e 4 segundos, janela total de 30 segundos e respeito a `Retry-After`.
- O servidor preserva a pergunta em falhas complementares. O histórico dispõe de até 6 segundos, comprador de até 1 segundo, sempre reservando tempo dentro dos 15 segundos totais. Uma tarefa complementar atrasada mantém sua vaga reservada até terminar.
- A renovação da conexão durante um complemento concluído acompanha as consultas seguintes. Resultados de trabalhadores atrasados não modificam o estado já devolvido.
- A admissão ocorre antes de enviar trabalho ao executor: 16 execuções globais, quatro por empresa; trabalho secundário limitado a oito globais e duas por empresa. A fila tem capacidade total de 64, deduplicação e descarte de tarefas cujo prazo expirou.
- Revogação comprovada elimina dados protegidos do cache. Respostas parciais preservam a idade original; uma atualização parcial antiga não substitui uma atualização completa recente.
- Ambas as rotas de geração manual, principal e v2, exigem pergunta, anúncio e histórico canônicos, no Codex e no legado. Informações de prontidão ou histórico enviadas pelo navegador não autorizam geração.
- O worker revalida identidade e permissões no contexto restrito da sessão original. O registro efêmero tem limite de 128 entradas e validade máxima de 15 minutos, limitada pelo vencimento da sessão. Após reinício ou expiração, a geração precisa ser solicitada novamente. Credenciais não são persistidas no job.
- Um bloqueio da geração conserva o rascunho do operador, remove referências a propostas obsoletas e explica a nova tentativa. Revogação durante a geração utiliza o mesmo tratamento por sessão, loja e recurso da consulta inicial.

## Contratos

Rotas e campos existentes preservados. O detalhe recebe o parâmetro opcional `componentes`, restrito a `question`, `history` e `buyer`. Esse parâmetro seleciona trabalho; somente snapshots canônicos autorizados e frescos podem ser reaproveitados.

Respostas acrescentam `components` e `item_states`. Erros incluem código, alcance e possibilidade de repetição nos cabeçalhos `X-JK-Error-*`, além de `Retry-After` quando aplicável. O hash interno de política da IA mudou para invalidar propostas anteriores ao preflight; a versão comercial permaneceu intacta.

## Verificações

Regressões foram registradas antes das respectivas implementações, incluindo cancelamento da paginação, reserva de capacidade, timeout convertido indevidamente em 502, perda de cache após negação de recurso, histórico incompleto e resposta HTTP 200 inválida.

Rodadas concluídas:

- 116 testes integrados de carregamento, componentes, agendamento, transporte, preflight, snapshot de lojas e contratos.
- Após a revisão final de idade de cache e renovação da conexão entre componentes, 105 testes de API, componentes, agendamento, transporte e preflight passaram. Uma retomada que ultrapassa a validade original é marcada como desatualizada.
- 365 regressões de compatibilidade, evidências, histórico, lojas, limites de resposta e contexto.
- 318 testes da suíte ampliada de IA, fila, solicitações e catálogo; duas exclusões preexistentes estão identificadas abaixo. Ajustes finais do preflight também passaram em sua rodada específica.
- 30 testes de segurança, evidências e guardrails.
- Contratos JavaScript de geração manual, persistência Codex, automação, última atualização, treinamento, solicitações, sidebar e espelhos HTML.
- Navegadores simulados de lojas e sidebar aprovados. Perguntas passou com A → B → A, atualização forçada, falhas parciais, intervalos de repetição, renovação de sessão e rejeição de contexto pela IA. A mensagem dessa rejeição permanece visível após renderizar novamente o cartão.
- Compilação dos 16 Python alterados/novos, sintaxe dos cinco JavaScript alterados/novos, `git diff --check` e conferência dos 24 espelhos HTML aprovados.

As contagens representam rodadas distintas e podem conter testes em comum.

### Reprodução dos testes direcionados

Usar o Python da `.venv` do checkout principal, com as dependências de teste disponíveis, e executar no worktree:

```text
pytest -q tests/test_perguntas_loading_api.py tests/test_perguntas_loading_components.py tests/test_perguntas_loading_scheduler.py tests/test_perguntas_loading_transport.py tests/test_perguntas_generation_preflight.py tests/test_perguntas_store_snapshot.py tests/test_perguntas_pos_venda_endpoints_contract_snapshot.py tests/test_perguntas_pos_venda_endpoints_architecture.py tests/test_codex_contract_snapshot_v2.py
node tests/perguntas_loading_recovery.js
node tests/perguntas_loading_browser.js
node tests/perguntas_stores_snapshot_browser.js
node tests/black_jhon_sidebar_browser.js
node scripts/sync-static-html-mirrors.js --check
git diff --check
```

O ambiente local exigiu `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` e acesso ao pytest do diretório de pacotes do usuário. Para os testes de navegador, `NODE_PATH` aponta para `node_modules` do checkout principal.

### Falhas preexistentes confirmadas

Dois testes de `tests/test_customer_reply_codex_orchestrator.py` falham também no HEAD original do dev por `Integracoes service context was not configured`:

- `test_canonical_question_item_and_history_are_reloaded_by_ids`
- `test_stale_request_item_cannot_be_marked_as_current_when_official_reload_fails`

Eles foram executados para comparação e excluídos da rodada ampliada de 318 testes. Não foram apagados, marcados como aprovados nem ocultados por alteração do código de produção. Os novos testes cobrem o bloqueio canônico e os caminhos manuais com contexto autenticado simulado.
