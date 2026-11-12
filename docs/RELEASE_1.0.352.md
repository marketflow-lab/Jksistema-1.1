# JK Sistema 1.0.352

Atualização pequena pelo canal `app-update`, reunindo as correções aprovadas de Importações, Pesquisa de Mercado e Devoluções.

- Calcula o custo dos produtos sem cubagem cadastrada usando o frete salvo, quando disponível.
- Mostra a nossa margem na lista de Importações, permite escolher as colunas visíveis e exibe a cubagem com até quatro casas decimais.
- Analisa quatro SKUs de Importações simultaneamente, com capacidade reservada ao detalhe aberto, cache compartilhado e proteção contra respostas superadas.
- Corrige a atualização das margens quando cálculos da lista e do detalhe terminam ao mesmo tempo.
- Pesquisa de Mercado verifica novamente os anúncios sem informações em até três rodadas, preservando os dados já confirmados.
- Devoluções limita a cinco as tentativas de consultas que falham repetidamente, informa a indisponibilidade e permite iniciar uma nova busca manual.

O `app-update` reutiliza o runtime compatível existente. O Setup completo acompanha a mesma release.
