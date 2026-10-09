# Participação em ofertas relâmpago com reanálise

A participação LIGHTNING usa o convite oficial selecionado e o contexto financeiro da análise concluída. O campo `price` de um candidato é uma sugestão; o vendedor define `deal_price` dentro dos limites oficiais. O servidor valida a proposta e recalcula a margem antes de decidir o envio.

## Política de preço e compatibilidade

O pedido de participação aceita dois campos adicionais:

- `analysis_job_id`: identificador da análise concluída.
- `price_policy`: `exact` ou `refresh_and_reanalyse`.

Clientes antigos continuam com `exact`: o preço selecionado precisa coincidir com o convite atual. A interface e a automação usam `refresh_and_reanalyse` para LIGHTNING, vinculando cada linha pelo `analysis_client_ref` recebido na análise.

Ambas as políticas conferem o preço-base standard do marketplace e os limites do convite. Os valores são comparados em centavos; o preço precisa ser positivo, respeitar os limites disponíveis e ser inferior ao preço-base. O preço de venda atual de `/sale_price` é mantido como informação separada. Uma promoção vigente pode alterar esse valor.

Na política `refresh_and_reanalyse`, um candidato explícito com sugestão positiva e limites mínimo e máximo válidos permite ajustar a proposta ao valor mais próximo da sugestão dentro da interseção entre os limites oficiais e o preço-base menos um centavo. Se a sugestão já for válida, ela é preservada. Sem ambos os limites, com limites conflitantes ou sem intervalo viável, não há ajuste automático. O valor original do convite permanece intacto, e `invitation_price` registra essa sugestão no diagnóstico; `official_price` registra a proposta validada. O ajuste depende da recotação financeira e da aprovação da margem e tolerância.

A validação imediatamente anterior ao envio recalcula a mesma proposta com o mesmo convite e os preços oficiais confirmados no servidor. A presença de dados no navegador não autoriza um preço diferente. O modo `exact` mantém a exigência de igualdade com a sugestão selecionada.

## Contexto financeiro no servidor

Os jobs do worker e do processamento em segundo plano guardam um contexto privado com cliente, loja, vendedor, site, campanhas, referências das linhas e entradas financeiras. A captura interna usa `ContextVar`, preservando as assinaturas HTTP de análise. São armazenados somente campos operacionais selecionados.

A reanálise recarrega o cadastro, resolve o custo da variação mais cara, consulta tarifa e frete no preço atualizado e renova a referência da Promoção 1. A decisão usa as funções existentes de margem mínima, tolerância e campanha programada, incluindo o tratamento vigente de tarifas estimadas.

Os custos e margens recebidos do navegador não aprovam a operação. Contexto ausente, incompleto, de outra conta ou incompatível com a linha exige uma nova análise. Resultados antigos e análises síncronas legadas sem esse contexto continuam sujeitos à política `exact`.

## Tentativas e confirmação

Um convite sem proposta válida é renovado uma vez. Persistindo a ausência de uma proposta dentro do intervalo permitido, a operação termina com diagnóstico antes do envio.

Após HTTP 400 com `ERROR_CREDIBILITY_DISCOUNTED_PRICE`, o mesmo convite é renovado uma vez. Uma segunda inscrição depende de outro preço oficial válido e aprovado pela margem recalculada. Se `candidate.price` continuar igual ao preço recusado, o servidor pode usar exclusivamente o `suggested_discounted_price` atual desse candidato. Essa sugestão precisa estar dentro dos limites oficiais, ser inferior ao preço standard e ser diferente de todos os preços já recusados; ela não é ajustada artificialmente. A camada imediatamente anterior ao envio revalida sua origem e seu valor em centavos. Há no máximo dois envios por convite. Não há redução progressiva, percentuais arbitrários, uso do limite mínimo como alternativa nem preço de outra campanha. Recomendações monetárias conflitantes para a mesma referência impedem o envio.

Timeout, resposta de servidor ou resultado sem confirmação levam somente a consultas da participação, sem reenviar a inscrição. A confirmação depende de conta, campanha, anúncio, preço, referência e status compatíveis, excluindo ofertas já existentes antes do envio e resultados ambíguos. Uma nova referência `OFFER` com status `pending` confirma a inscrição mesmo quando o calendário ainda não está disponível.

Resultados inconclusivos recebem um identificador opaco de confirmação. O servidor preserva em `promo_jobs` apenas o contexto operacional mínimo da tentativa, vinculado ao cliente, loja, vendedor, análise e referência da linha; não guarda token nem resposta bruta. O contexto vale por 24 horas. A rota adicional `GET /api/promo/confirmar-participacoes/{confirmation_id}` recupera esse contexto e nunca aceita preço, oferta ou conta enviados pelo navegador. Troca de cliente, loja ou vendedor impede a consulta.

Uma consulta adicional ocorre ao final do lote. Enquanto o modal estiver aberto, a interface acompanha as pendências por até 60 segundos, com intervalos de 2, 5 e depois 10 segundos. O botão “Verificar confirmação” permite consultar novamente. Cada chamada ao servidor tem orçamento de 6 segundos e atualiza somente resultados vinculados ao contexto original. Fechar o modal ou alterar cliente, loja ou análise encerra o acompanhamento. O resultado confirmado não volta para pendente, e consultar uma confirmação já obtida não gera nova requisição ao Mercado Livre.

A regra interna de credibilidade do Mercado Livre pode manter uma recusa mesmo após todas as validações. Nesse caso, a operação termina com a evidência identificada.

## Resultado na interface

Cada detalhe pode incluir `analysis_row_updates` e `price_diagnostics`, com preços inicial, oficial e enviado, preço-base, preço de venda, limites, margem recalculada, quantidade de tentativas, código da decisão, código do Mercado Livre e status HTTP.

A interface atualiza somente a linha vinculada ao job, campanha, cliente, loja e referência. Pendências de confirmação aparecem separadas de recusas; uma confirmação posterior atualiza contagens, agenda e aviso “Já participa” da campanha de destino, preservando o Status da promoção de referência. Decisões, filtros, ordem e controles de seleção são preservados. O resumo conta cada convite uma vez; tentativas adicionais aparecem no diagnóstico.

Alterar loja, campanhas, mínimo ou tolerância invalida o contexto para ajuste automático. A automação do servidor também verifica a configuração atual antes de aplicar um job anterior.

## Validação

As regressões usam respostas simuladas e bloqueiam a rede. Incluem os casos de R$ 97,65 e R$ 62,95 recusados com sugestões alternativas oficiais, além de R$ 147,46 retornando HTTP 201 e aparecendo como oferta programada somente em consultas posteriores. Aceitação simulada não constitui confirmação de uma nova inscrição real no Mercado Livre. Cobrem os preços fornecidos na investigação, limites, recusa mantida, novo preço aprovado ou reprovado, recotação financeira, variações, isolamento do contexto, duplicidade, timeout, resposta 201 inconclusiva e confirmação manual/automática. As verificações da interface incluem atualização pontual, escape de mensagens, múltiplas datas e preservação de seleções.

Referências: [ofertas relâmpago](https://developers.mercadolivre.com.br/pt_br/ofertas-relampago) e [API oficial de preços](https://developers.mercadolivre.com.br/devcenter/api-de-precos).
