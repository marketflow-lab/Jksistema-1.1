# Tarifa ML estimada na análise de promoções

A análise passa a exibir uma tarifa aproximada quando a tarifa final da campanha
não pode ser confirmada. A tela destaca **Estimado** em amarelo junto da tarifa
e, quando calculados com ela, do valor líquido e da margem. O motivo da estimativa
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

Lucro e margem estimados exigem custo, imposto e frete disponíveis e válidos,
com o frete correspondente ao mesmo preço. A fórmula preserva o cálculo existente:
preço menos tarifa, frete, custo e imposto. Valores negativos de lucro e margem
continuam possíveis; entradas numéricas inválidas não viram resultados.

## Integração

Os três caminhos de análise (direta, job e com arquivos) transportam os campos
`_jk_tarifa_ml_estimada`, `_jk_tarifa_ml_estimativa_motivo`,
`action_financeiro_estimado` e `action_financeiro_estimativa_motivo`.
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

Resultado da suíte Python de promoções: 285 testes e 87 subtestes aprovados.
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
