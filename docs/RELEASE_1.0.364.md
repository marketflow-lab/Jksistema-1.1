# JK Sistema 1.0.364

Atualização pequena pelo canal `app-update`, com seleção de células no estilo Excel em Médias e Pedidos.

- Clique seleciona a célula inteira; arrastar seleciona um retângulo entre linhas e colunas. Shift amplia o intervalo, Ctrl permite selecionar células separadas e as setas navegam pela tabela. Ctrl+A seleciona os dados e Ctrl+C copia as células com colunas e linhas para colar no Excel. Escape limpa a seleção.
- O balão acompanha a seleção e mostra a soma e a quantidade de valores numéricos. SKU, títulos e legendas auxiliares ficam fora do cálculo. A seleção preserva as marcações ao rolar e inclui os valores das linhas fora da janela virtual, tanto na soma quanto na cópia.
- Os botões de edição de sugestão de compra e as demais ações da tabela continuam disponíveis.

A base inclui a correção aprovada que considera provisoriamente pedidos em análise na sugestão de compra, com o mesmo cálculo na tabela, na lista e na exportação. No cenário de regressão com 700 unidades em análise, a sugestão continua em 370 unidades. Também inclui o catálogo comum de modelos de IA e as correções de Perguntas, Avant Pro, Vendas e BlackJohn já integradas até a 1.0.363.

Validação: seleção por mouse e teclado, intervalos e células separadas, cópia TSV, virtualização, soma de linhas fora da tela, edição, previsão de compras e contratos de distribuição. Versões dos pacotes e lockfiles, manifesto de contexto e contratos de versão atualizados para 1.0.364.

Publicação pelo workflow `desktop-release.yml` com `release_mode=app-update` e `publish=true`, incluindo os gates de Setup, Update e reutilização do runtime. O contrato de runtime permanece compatível com as versões anteriores.
