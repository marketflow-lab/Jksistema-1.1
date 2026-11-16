# JK Sistema 1.0.356

Atualização pequena pelo canal `app-update`, consolidando as correções aprovadas e publicadas até a versão 1.0.355.

- Inclui a navegação por Ctrl+clique, a limitação das tentativas dos motivos de devolução, os ajustes de filtros e gráficos de Vendas e as melhorias de custos, margens e anúncios de Importações.
- Inclui as melhorias de carregamento e recuperação de fotos de Médias e Vendas, a conexão central do Bling e as correções da Pesquisa de Mercado e do Avant Pro.
- Preserva a coleta paralela e o tratamento de anúncios de catálogo sem comprovação individual do vendedor.
- Atualiza os contratos de validação para reconhecer os campos e as rotas opcionais de Importações já aprovados, mantendo os hashes dos contratos anteriores.
- Alinha o teste de Packing List à apresentação aprovada de quatro casas decimais de cubagem, verificando também a preservação do valor bruto.
- Torna determinístico o teste de validade do cache de margens, compartilhando o mesmo relógio entre a API simulada e o navegador e preservando as verificações de consultas e novas tentativas.

A auditoria das conversas e dos commits confirmou que os Worktrees antigos mantêm cópias históricas de alterações já integradas. Essas cópias não foram reaplicadas. Alterações locais sem relação com a release foram preservadas.

O `app-update` reutiliza o runtime compatível existente, sem modificar seu contrato. O Setup completo acompanha a mesma release conforme o workflow de distribuição.

Validação local: 314 testes Python aprovados, regressões da Pesquisa de Mercado, Ctrl+clique no Electron 28.3.3 e no navegador, cache de margens e Packing List em 15 tamanhos de tela. Gates de dependências, contrato de runtime, configuração do Update, materialização, inicialização do backend, extração WiX, seleção de artefatos, manifesto de contexto e espelhos HTML aprovados. Compilação dos testes Python alterados, sintaxe JavaScript e `git diff --check` concluídos.

O aplicativo instalado não faz parte desta atualização do ambiente de desenvolvimento.
