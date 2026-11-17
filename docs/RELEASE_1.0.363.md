# JK Sistema 1.0.363

Atualização pequena pelo canal `app-update`, com as alterações aprovadas de Médias e Pedidos e do catálogo de modelos de IA.

- A sugestão de compra considera provisoriamente as quantidades pendentes dos pedidos em análise que já aparecem na coluna Em trânsito. O balão explica que o estoque previsto depende da confirmação da compra e do prazo. Essas quantidades não garantem estoque antes da chegada, e entregas previstas para depois dela não reduzem a nova compra.
- O cálculo continua descontando recebimentos parciais, respeitando a loja selecionada e usando o mesmo resultado na tabela, na lista de compra e na exportação. No cenário de regressão com previsão de 152,2 unidades por mês e 700 unidades em análise sem data, a sugestão passa de 1.070 para 370 unidades.
- Selecionar valores numéricos na tabela de Médias exibe um balão com a soma e a quantidade de valores. Números de SKU, títulos e legendas auxiliares não entram nessa soma.
- Configurações, BlackJohn, Vendas, Perguntas, Pós-venda, Favoritos e WhatsApp usam o catálogo comum de modelos GPT e os níveis de raciocínio suportados por cada modelo, conforme a atualização já aprovada no desenvolvimento.

Esta versão preserva as correções de Perguntas, Avant Pro, carregamento de Vendas e BlackJohn já consolidadas na base 1.0.362. O contrato do runtime permanece igual; o Update reutiliza o runtime compatível existente.

Validação local: testes de previsão, integração, recebimentos, edição, cache e interface de Médias; catálogo e provedores de IA, evidências, conflitos, isolamento, prompt injection, timeout e fallback; distribuição, dependências, espelhos HTML, manifesto de contexto e gates de empacotamento. Compilação dos Python alterados e `git diff --check` conferidos.

As versões dos pacotes, lockfiles, manifesto do contexto e contratos de distribuição acompanham a 1.0.363. O workflow usa `release_mode=app-update` e `publish=true`, verifica Setup e Update, testa a reutilização do runtime e publica os artefatos. A cópia instalada nesta máquina é preservada.
