# Tarifa ML estimada na análise de promoções

A análise passa a exibir uma tarifa aproximada quando a tarifa final da campanha
não pode ser confirmada. A tela destaca **Estimado** em amarelo junto da tarifa,
do benefício ML aproximado efetivamente abatido e, quando calculados com ela,
do valor líquido e da margem. O motivo da estimativa
fica disponível ao passar o mouse ou focar o destaque. A exportação XLSX acrescenta
`(Estimado)` aos mesmos valores.

## Critério de cálculo

1. A tarifa confirmada continua tendo prioridade.
2. Sem confirmação, usa a cotação `listing_prices` consultada para o preço da
   campanha selecionada. Se vier somente a composição completa, reconstrói a
   tarifa com o percentual total e a taxa fixa retornados na consulta.
3. Considera o benefício ML validado da mesma oferta. Para campanhas compatíveis,
   também aceita uma aproximação da participação ML informada: percentual sobre
   o preço original, limitado pelo desconto total, ou parcela monetária
   reconciliada com a participação do vendedor. Essa aproximação permanece
   identificada como estimativa.
4. Havendo divergências na oferta ou participação incompatível, usa a tarifa-base
   consultada e informa o motivo. Se faltarem os dados mínimos da própria tarifa,
   mantém `A calcular`.

O valor total retornado pela consulta já é usado como total: taxa fixa e
parcelamento não são somados novamente. A reconstrução por componentes exclui
taxas de publicação e a tabela fixa antiga de contingência. Benefícios já
aplicados também não são abatidos uma segunda vez.

### Correção do desconto ML — 01/10/2026

A base monetária do benefício é resolvida depois da seleção da oferta definitiva,
priorizando seu `original_price` positivo e finito. Ela é independente da base
usada na promoção 1. Sem uma base válida na oferta, a base atual do anúncio pode
servir como contingência; o benefício calculado assim permanece estimado e não
confirma a tarifa nem autoriza participação automática.

O estimador retorna o benefício somente quando o abate da tarifa consultada.
Tarifa-base, conflito e tarifa já ajustada sem desconto identificável mantêm o
benefício ausente. A tarifa exata da API conserva prioridade, inclusive quando
o benefício que a compõe não é informado. Benefícios confirmados conservam
prioridade sobre estimativas. Zero explícito é apresentado como `R$ 0,00`;
ausência continua como `Não informado pela API`.

A coluna `Desconto ML` agora apresenta o benefício estimado com o selo amarelo
`Estimado`; o XLSX acrescenta `(Estimado)`. O desconto já faz parte da tarifa
final e não é somado novamente ao lucro ou à margem.

Casos de referência sintéticos: oferta com base de R$ 120, preço de R$ 96,
vendedor 18% e ML 2% resulta em R$ 2,40 nos três modos, mesmo que a base atual
do anúncio seja R$ 100. Com base de R$ 149,24 e participação parcial ML de 2,4%,
o benefício estimado é R$ 3,58 e a tarifa de R$ 18,83 passa a R$ 15,25.

Lucro e margem estimados exigem custo, imposto e frete disponíveis e válidos,
com o frete correspondente ao mesmo preço. A fórmula preserva o cálculo existente:
preço menos tarifa, frete, custo e imposto. Valores negativos de lucro e margem
continuam possíveis; entradas numéricas inválidas não viram resultados.

## Integração

Os três caminhos de análise (direta, job e com arquivos) transportam os campos
`_jk_tarifa_ml_estimada`, `_jk_tarifa_ml_estimativa_motivo`,
`action_financeiro_estimado` e `action_financeiro_estimativa_motivo`.
Os resultados também transportam `action_desconto_ml` (número ou `null`),
`_jk_desconto_ml_estimado` (booleano), `_jk_desconto_ml_estimativa_motivo`
(texto), `_jk_desconto_ml_confiavel` e `_jk_desconto_ml_fonte`. Valor ausente
mantém `null`, flags falsas e textos vazios, sem transformar ausência em zero.
O caminho com jobs conserva esses campos no resultado da análise. Os metadados
são aditivos; rotas, parâmetros, nomes e ordem das colunas permanecem compatíveis.
Os números da API permanecem sem o rótulo textual. Os resultados aproximados
mantêm `action_financeiro_exato=false`; a participação automática continua
exigindo os critérios existentes, e a escolha manual permanece disponível.

A identificação de cliente, loja, anúncio e campanha é validada pelo fluxo
existente antes da consulta. O estimador não procura valores em outras lojas,
anúncios ou preços. O tipo de campanha confirmado pelo adaptador também pode
ser usado quando não é repetido no corpo da oferta.

## Verificação e entrega

Casos cobertos: benefício desconhecido ou parcial, componentes da tarifa,
parcelamento e taxa fixa sem duplicidade, benefício já aplicado, divergências,
zero, entradas inválidas, componentes ausentes, prioridade da tarifa confirmada,
campanha divergente, metadados nos três caminhos, exportação XLSX e destaque no
navegador. Os testes usam dados sintéticos; não comprovam o valor real dos
anúncios da captura.

Na correção de 01/10/2026, a suíte de promoções, o versionamento do worker e os
testes de segurança/sequência de promoções dos favoritos passaram com 357 testes
e 87 subtestes. Os nove scripts JavaScript de promoções também passaram, incluindo
os testes de navegador para desconto, tarifa, margem e participação. Os seis
Python alterados foram compilados; sintaxe JavaScript e `git diff --check`
passaram. O aviso preexistente do Pydantic em `promocoes_api_jobs.py` permanece.

A base desta correção é `f2d1289b417ff3bb9395c97f44e8dc1cf6b1fbf6`. Os hashes
de rotas e schemas listados abaixo foram conferidos e preservados. O contrato de
efetivação dos favoritos também conserva o hash
`a72c3c115b15e52f5af66c70984849442885bf62`.

Resultado da implementação original de 16/09/2026: 285 testes e 87 subtestes aprovados.
Houve um aviso de depreciação preexistente do Pydantic em `promocoes_api_jobs.py`.
Compilação dos módulos Python alterados e verificação de whitespace aprovadas.
Os nove scripts JavaScript de promoções também passaram. O teste de navegador
confere que o rótulo e seu texto ficam inteiros em células efetivas de 65 px.

Base do worktree: `72f980898d838e869c636f038ec897ea74c674ef`.
Rotas e schemas públicos preservados (hashes Git da base):

- `backend/routers/promocoes.py`: `21f20513bfd9a829c4240e62d1ac3e89084eec6c`.
- `backend/schemas/promocoes.py`: `4630efc67e6ed683f974233fed7e573c63966aa9`.

Referência dos componentes consultada durante a implementação:
[Comissão por vender — Mercado Livre](https://developers.mercadolivre.com.br/pt_br/comissao-por-vender).
