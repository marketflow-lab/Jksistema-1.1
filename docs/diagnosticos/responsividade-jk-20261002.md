# Correção da responsividade do JK Sistema

Implementação em Worktree dedicado, criado a partir de `43f9f590d758a170276db93726007d8111130b64` e depois atualizado para `3d0811dc2725d6065db083186b6230706b30fddd`, que chegou ao checkout principal durante o trabalho. As mudanças aprovadas de pesquisa de anúncios e fornecedor obrigatório foram preservadas. A branch `codex/correcao-responsividade` contém um commit somente local. A primeira aprovação foi o pedido de implementação do plano. A transferência para o checkout principal depende da segunda aprovação após a revisão do diff.

A seleção do WhatsApp descarta histórico resolvido, pós-venda, registros incompletos e lojas desabilitadas antes de consultar jobs. Os candidatos são deduplicados e lidos em um lote por cliente/ciclo, com uma conexão SQLite somente leitura. O leitor não prepara schema, grava metadados ou calcula métricas da fila. A decisão e o envio usam revalidação fresca. Tokens ativos mantêm consultas individuais somente leitura para preservar estados `sending` e pesquisas em andamento; essas consultas podem acrescentar conexões além do lote de seleção. `get_job()` e o contrato da IA foram preservados.

Importações, catálogo e cálculos executam em workers com duas operações pesadas simultâneas por loop do servidor. A fila aguarda de forma assíncrona. Páginas, autenticação e leituras curtas de lojas têm capacidade independente. Os ContextVars seguem para o worker. A tarefa dona da vaga permanece ativa até terminar, inclusive após cancelamento bruto de asyncio. Uploads são lidos uma vez e seus bytes podem ser reutilizados na recomputação. Na base nova, os quatro handlers do cadastro de fornecedores também usam workers para que a espera pelo mutex compartilhado com as listas ocorra fora da thread do servidor.

Os escritores de listas compartilham o mutex do caminho canônico: edição, aprovação, importação, sugestões, backfill do GET e reposição do BlackJohn. No commit, o arquivo é relido e comparado com o snapshot. Alterações em outras listas são preservadas. Mudança nos dados usados provoca uma recomputação; um segundo conflito retorna 409. Sugestões calculam o trânsito a partir do mesmo snapshot validado no commit. JSON e cache XLSX usam temporário, fsync e substituição atômica. A chave do XLSX inclui hash do conteúdo para distinguir edições com o mesmo timestamp. A coordenação por caminho cobre os escritores no mesmo processo, como o coordenador existente do projeto.

O watcher do Context Hub inicializa uma vez e lê as configurações por SQLite somente leitura. Banco ausente, incompatível, trocado ou com recuperação pendente suspende a varredura. A recuperação tenta novamente após cinco segundos sob os locks existentes. A cache estática tem quatro entradas e limite total de 64 MiB. A chave inclui raiz, superfície, versões e hash completo das fontes permitidas, com validação antes e depois da construção. SKU, capacidades, curadoria, evidências e informações do cliente são lidos novamente. Reconstrução forçada ignora a cache. Outbox, publicação atômica, rollback e `80_Curadoria` permanecem no fluxo existente.

## Medições equivalentes em ambiente sintético

| Carga e medição | Anterior | Corrigido |
|---|---:|---:|
| Servidor: duração de dois trabalhos de 1,5 s | 3.022,09 ms | 1.525,64 ms |
| Servidor: CPU do processo | 187,50 ms | 171,88 ms |
| Servidor: maior latência e p95 de 15 consultas | 2.994,41 ms | 18,77 ms |
| WhatsApp: conexões SQLite por seleção e revalidação | 6.003 | 2 |
| WhatsApp: duração mediana do ciclo | 149.545,259 ms | 48,213 ms |
| WhatsApp: CPU mediana do ciclo | 44.203,125 ms | 31,250 ms |
| Inventário: duração de seis construções | 14,421371 s | 7,474481 s |
| Inventário: CPU de seis construções | 11,921875 s | 6,625000 s |
| Inventário: análises AST nos seis ciclos | 750 | 0 |
| Watcher: conexões em seis leituras | 18 | 6 |
| Watcher: comandos de schema/metadados | 252 | 0 |
| Watcher: duração das seis leituras | 0,623431 s | 0,258298 s |
| Watcher: CPU das seis leituras | 0,500000 s | 0,171875 s |

O benchmark do servidor usa Uvicorn real e as mesmas 15 consultas HTTP nos dois modos. Mede isolamento e agendamento. O WhatsApp usa 2.000 aprovações históricas e quatro pendentes, três rodadas, seleção idêntica e revalidação fresca no modo corrigido; a carga não inclui tokens ativos. O inventário compara reconstruções forçadas sem reuso com cache aquecida: 120 arquivos com 50 funções por arquivo, seis ciclos e releitura de capacidades a cada ciclo. Os dados são sintéticos e os tempos não representam medição do aplicativo instalado.

Reprodução na raiz do Worktree:

```powershell
python -B scripts/diagnosticos/benchmark_server_responsiveness.py
python -B scripts/benchmark_question_approvals.py --historical 2000 --pending 4 --rounds 3 --full-warmup
python -B tests/helpers/benchmark_context_inventory.py
```

A medição completa do WhatsApp imprimiu as três amostras antes de um erro de limpeza por conexão aberta no seed. O fechamento foi corrigido. A validação curta posterior terminou com saída 0, seleção idêntica e limpeza confirmada. As métricas completas acima foram obtidas antes desse erro de limpeza.

## Verificação

Resultados por rodada, com sobreposição entre suítes:

| Rodada | Resultado |
|---|---|
| WhatsApp, rascunhos protegidos e lifecycle | 165 passaram |
| Leitor somente leitura e orquestrador completo | 212 passaram |
| IA: rascunho parcial, fontes conflitantes e conteúdo malicioso | 25 passaram |
| Context Hub, inventário, contratos, arquitetura e governança | 81 passaram; 1 ignorado |
| Cache, watcher, evidências e exclusividade de locks | 45 passaram |
| Contratos, arquitetura, cache e watcher finais | 41 passaram |
| Limite total de memória da cache | 1 passou |
| Trânsito, sugestões e overrides | 14 passaram |
| Transações, relatórios avançados, trânsito e conexão compartilhada | 72 passaram |
| Responsividade HTTP e cancelamentos finais | 6 passaram |
| Lojas e catálogo com runtime correto e CSV sintético | 1 passou |
| Pós-rebase: IA, WhatsApp, leitor RO, lifecycle e sealed | 359 passaram |
| Pós-rebase: integridade, reposição e relatórios | 45 passaram |
| Pós-rebase: trânsito, sugestões e fornecedor preservado | 15 passaram |
| Pós-rebase: workers e fornecedores | 42 passaram; cenário de 50 s aprovado antes nesta base |
| Pós-rebase: CRUD do cadastro de fornecedores | 3 passaram |
| Pós-rebase: Context Hub, cache, watcher, contratos e arquitetura | 41 passaram |

O teste HTTP mantém dois workers bloqueados por 50 segundos e uma terceira operação em espera. `/health`, Dashboard, Vendas, PPV, Importações, autenticação síncrona e lojas respondem em menos de um segundo durante a carga. Os casos verificam o máximo de duas execuções, ContextVars, cliente, cancelamento AnyIO e cancelamento bruto de asyncio. Quatro novos casos Uvicorn exercitam GET, POST, PUT e DELETE de fornecedores com o mutex real retido por outra thread: saúde e Dashboard continuam abaixo de um segundo, com cliente preservado e CRUD persistido corretamente.

Os testes de integridade exercitam APIs reais de edição, aprovação, exclusão e importação durante o backfill. Verificam preservação das alterações, recomputação única, segundo conflito 409, commit após cancelamento e arquivos íntegros. IA cobre ausência de evidência, fontes conflitantes, prompt injection, timeout, contrato antigo, bloqueio, cancelamento e rascunhos expirados ou inválidos.

A cache foi exercitada com mudanças de mesmo tamanho e mtime, inclusão, remoção, edição durante construção, force, cópias isoladas, construção concorrente e limites de memória. O watcher foi exercitado com troca de banco, schema incompatível, banco ocupado, pausa/debounce, journal e recuperação sob locks. Um teste de symlink real foi ignorado por falta de privilégio do Windows; junctions simuladas passaram.

A comparação completa de 183 testes de trânsito, compatibilidade do cadastro legado e autenticação do cadastro produziu exatamente os mesmos resultados no commit inicial exportado isoladamente e no Worktree: 45 passaram e 138 falharam. Os nodeids e resultados foram idênticos, com zero regressões novas. As fixtures antigas não configuram o leitor operacional de Integrações. A comparação utilizou dados temporários e não iniciou `backend_api`.

Os três contratos versionados tiveram seus hashes comparados com a base e permaneceram iguais. A comparação AST das 121 assinaturas públicas não encontrou alterações. As definições do contrato da IA e o corpo de `get_job()` também permaneceram iguais. A compilação dos 37 Python alterados e `git diff --check` passaram no fechamento. As contagens também estão no arquivo de métricas agregado.

O aplicativo instalado e os dados operacionais continuam na versão existente. Esta entrega prepara o diff para a segunda aprovação, sem publicação ou transferência para o checkout principal.
