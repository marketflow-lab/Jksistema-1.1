# JK Sistema 1.0.357

Atualização pequena pelo canal `app-update`, com a correção aprovada da atualização automática de Vendas e as correções aprovadas das conversas anteriores já consolidadas na versão 1.0.356.

- A opção Atualizar automaticamente passa a mostrar a configuração salva da loja selecionada, inclusive ao reabrir a tela. A seleção Todas as lojas mostra quando somente parte das lojas está habilitada.
- Marcar ou desmarcar a opção salva imediatamente a configuração. A ativação retoma o período preservando os dias confirmados; a desativação pausa as tarefas automáticas da seleção e preserva tarefas manuais, outras lojas e checkpoints.
- A atualização manual aguarda a leitura inicial da configuração e preserva o estado individual das lojas. Falhas de gravação apresentam uma mensagem e mantêm o último estado confirmado.
- Os dois layouts de Vendas recebem a correção e novas versões dos arquivos de interface para renovar o cache.
- A conferência das conversas e versões evita reaplicar cópias antigas dos Worktrees. As correções já publicadas de Pesquisa de Mercado, Importações, Médias, fotos, navegação por abas e motivos de devolução permanecem incluídas.

Os arquivos de versão e o manifesto do bundle de contexto são atualizados em conjunto. O Update reutiliza o runtime compatível existente; o contrato de runtime permanece igual. O Setup completo acompanha a release conforme o workflow de distribuição.

Validação local: 324 testes Python aprovados, com um teste de junction ignorado por falta dessa permissão no ambiente; os 20 scripts de regressão de Vendas aprovados, incluindo 223 verificações do navegador nos dois layouts; nove controles de dependências, runtime, configuração do Update, materialização, bootstrap, WiX, staging e manifesto aprovados. O teste de cache persistente passou isoladamente após um timeout sob execução paralela de navegadores. A regressão do layout aguarda a renderização do frame antes de conferir o controle, e os contratos de cache reconhecem a nova versão dos arquivos. Compilação Python, sintaxe JavaScript, espelhos HTML e `git diff --check` concluídos.

A aplicação no ambiente de desenvolvimento e a publicação foram autorizadas. O aplicativo instalado está fora do escopo desta atualização.
