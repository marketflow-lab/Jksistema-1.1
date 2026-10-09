# Participação em ofertas relâmpago com reanálise

A participação LIGHTNING usa o convite oficial selecionado e o contexto financeiro da análise concluída. Quando o Mercado Livre informa outro preço para esse convite, o servidor recalcula a proposta antes de decidir o envio.

## Política de preço e compatibilidade

O pedido de participação aceita dois campos adicionais:

- `analysis_job_id`: identificador da análise concluída.
- `price_policy`: `exact` ou `refresh_and_reanalyse`.

Clientes antigos continuam com `exact`: o preço selecionado precisa coincidir com o convite atual. A interface e a automação usam `refresh_and_reanalyse` para LIGHTNING, vinculando cada linha pelo `analysis_client_ref` recebido na análise.

Ambas as políticas conferem o preço-base standard do marketplace e os limites do convite. Os valores são comparados em centavos; o preço precisa ser positivo, respeitar os limites disponíveis e ser inferior ao preço-base. O preço de venda atual de `/sale_price` é mantido como informação separada. Uma promoção vigente pode alterar esse valor.

## Contexto financeiro no servidor

Os jobs do worker e do processamento em segundo plano guardam um contexto privado com cliente, loja, vendedor, site, campanhas, referências das linhas e entradas financeiras. A captura interna usa `ContextVar`, preservando as assinaturas HTTP de análise. São armazenados somente campos operacionais selecionados.

A reanálise recarrega o cadastro, resolve o custo da variação mais cara, consulta tarifa e frete no preço atualizado e renova a referência da Promoção 1. A decisão usa as funções existentes de margem mínima, tolerância e campanha programada, incluindo o tratamento vigente de tarifas estimadas.

Os custos e margens recebidos do navegador não aprovam a operação. Contexto ausente, incompleto, de outra conta ou incompatível com a linha exige uma nova análise. Resultados antigos e análises síncronas legadas sem esse contexto continuam sujeitos à política `exact`.

## Tentativas e confirmação

Um convite incoerente é renovado uma vez. Persistindo a incoerência, a operação termina com diagnóstico antes do envio.

Após `ERROR_CREDIBILITY_DISCOUNTED_PRICE`, o convite pode ser renovado uma vez. Uma segunda inscrição depende de outro preço oficial do mesmo convite, coerente e aprovado pela margem recalculada. Há no máximo dois envios por convite na operação. Valores sugeridos, mínimos, percentuais calculados arbitrariamente e propostas de outras campanhas não servem como recuperação.

Timeout, resposta de servidor ou resultado sem confirmação levam à consulta da participação. A confirmação depende de identidade, preço e status compatíveis. Convites identificados podem continuar com agenda pendente, conforme o fluxo existente.

A regra interna de credibilidade do Mercado Livre pode manter uma recusa mesmo após todas as validações. Nesse caso, a operação termina com a evidência identificada.

## Resultado na interface

Cada detalhe pode incluir `analysis_row_updates` e `price_diagnostics`, com preços inicial, oficial e enviado, preço-base, preço de venda, limites, margem recalculada, quantidade de tentativas, código da decisão, código do Mercado Livre e status HTTP.

A interface atualiza somente a linha vinculada ao job, campanha, cliente, loja e referência. Decisões, filtros, ordem e controles de seleção são preservados. O resumo conta cada convite uma vez; tentativas adicionais aparecem no diagnóstico.

Alterar loja, campanhas, mínimo ou tolerância invalida o contexto para ajuste automático. A automação do servidor também verifica a configuração atual antes de aplicar um job anterior.

## Validação

As regressões usam respostas simuladas e bloqueiam a rede. Cobrem os preços fornecidos na investigação, limites, recusa mantida, novo preço aprovado ou reprovado, recotação financeira, variações, isolamento do contexto, duplicidade, timeout, resposta 201 inconclusiva e confirmação manual/automática. As verificações da interface incluem atualização pontual, escape de mensagens, múltiplas datas e preservação de seleções.

Referência: [API oficial de preços](https://developers.mercadolivre.com.br/devcenter/api-de-precos).
