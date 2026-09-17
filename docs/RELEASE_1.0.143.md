# JK Sistema 1.0.143

- Respostas de pré e pós-venda do Mercado Livre passam por um agente unificado, com contexto integral, histórico da conversa e até duas rodadas de pesquisa somente leitura.
- A pesquisa identifica publicamente o produto quando fabricante ou modelo não constam no cadastro e continua mesmo quando o modelo retorna texto prematuro junto com um pedido válido de pesquisa.
- Evidências do SKU exato, do fabricante e modelo ou de consenso técnico podem sustentar a resposta; consenso técnico exige revisão humana.
- Falhas internas, timeout, contrato inválido ou pesquisa indisponível não geram negativas genéricas para o comprador.
- As respostas preservam a unidade, a terminologia e o nível de detalhe usados pelo comprador, conciliando o histórico do mesmo comprador no mesmo anúncio.
- A IA recebe os atributos atuais do anúncio e as diretrizes oficiais atuais do Mercado Livre, com isolamento de tenant, loja, seller, site, SKU, item, variação e pedido.
- A análise de promoções mostra a tarifa estimada do Mercado Livre e mantém a indicação de participação restrita às campanhas realmente confirmadas.
- Leituras de loja continuam disponíveis durante atualizações, sem liberar mutações fora do fluxo autorizado.

[Validação desta versão](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.143/docs/RELEASE_1.0.143_VALIDACAO.md).

Esta versão incorpora e reconcilia o histórico das versões 1.0.141 e 1.0.142 com a branch atual de desenvolvimento.
