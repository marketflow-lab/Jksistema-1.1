# JK Sistema 1.0.355

Atualização pequena pelo canal `app-update`, com as correções aprovadas da Pesquisa de Mercado.

- Reconsulta somente falhas temporárias; anúncios já validados não entram novamente na fila.
- Encerra reconsultas automáticas quando a leitura do Avant tem métricas suficientes, mas não comprova o vendedor do catálogo, ou apresenta conflito explícito de vendedor.
- Mantém painéis Avant vazios ou parciais elegíveis para recuperação, sem atribuir vendas de outro vendedor.
- Preserva as decisões de reconsulta ao pausar e retomar; uma recuperação completa explícita inicia uma nova coleta das pendências.
- Conclui a leitura individual usando a estabilidade dos dados do anúncio, evitando timeouts causados por imagens e carrosséis que alteram a altura da página.
- Exibe o status compacto “Anuncio de catálogo”, com a explicação completa ao passar o mouse.
- Detalha no progresso quantos anúncios foram consultados, validados, continuam pendentes e encerraram as reconsultas automáticas.

Inclui as correções publicadas até a versão 1.0.354, com até quatro consultas simultâneas e a extensão Avant Pro 7.33.5. O `app-update` reutiliza o runtime compatível existente; o Setup completo acompanha a mesma release.

Validação local concluída: 25 arquivos de regressão JavaScript da Pesquisa de Mercado; 414 testes Python aprovados e um ignorado por falta de permissão de junction/symlink; oito gates de dependências, runtime e empacotamento; compilação dos Python alterados e `git diff --check`.

O teste integrado de histórico manteve suas verificações funcionais. Nesta máquina, a inicialização do servidor sintético levou 39 segundos; a execução de validação ampliou apenas esse limite de inicialização, em memória, para 120 segundos, sem alterar o teste versionado.

O Quality Gate amplo da base 1.0.354 já apresentava cinco falhas: contratos de schema/rotas, isolamento de SQLite, cache de margens e cubagem de Packing List. Essas áreas não receberam alterações nesta release.
