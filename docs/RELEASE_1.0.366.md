# JK Sistema 1.0.366

Atualização pequena pelo canal `app-update`, com a paginação aprovada da Pesquisa de Mercado e as correções aprovadas das conversas anteriores já publicadas até a versão 1.0.365.

- Pesquisa de Mercado apresenta números de página clicáveis e botões Primeira, Anterior, Próxima e Última.
- Dois cliques no número da página atual permitem digitar a página desejada no mesmo lugar. Enter confirma; Escape ou sair do campo cancela. Entradas inválidas recebem uma indicação sem consultar a API.
- Os rankings geral e por vendedor mantêm suas páginas independentes. Filtros reiniciam a paginação, e atualizações da coleta preservam o campo, o foco e a seleção durante a edição.
- A barra acompanha o aumento de páginas durante a coleta e recupera a última página válida quando o total diminui.
- Mantém as correções anteriores de Médias e Pedidos, BlackJohn, Sala de reunião, Vendas, Perguntas, Importações e Avant Pro já integradas e aprovadas.

Validação: navegação e edição de páginas, limites, filtros, rankings independentes, progresso da coleta, regressões do módulo e contratos de versão e distribuição.

Versões dos pacotes, lockfiles, manifesto de contexto e contratos de versão alinhados em 1.0.366. Publicação pelo workflow `desktop-release.yml` com `release_mode=app-update` e `publish=true`, incluindo a verificação de Setup, Update e reutilização do runtime existente. O contrato de runtime permanece compatível.
