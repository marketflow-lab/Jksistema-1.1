# JK Sistema 1.0.351

Atualização pelo canal `app-update` com as otimizações da análise de margem de Importações.

- Compartilha o cache financeiro entre a lista e o detalhe do SKU, evitando repetir análises com os mesmos dados.
- Mantém a última margem compatível visível durante atualizações em segundo plano e falhas temporárias.
- Confere os preços separadamente do cálculo financeiro e reaproveita respostas demoradas quando os insumos continuam compatíveis.
- Reutiliza consultas de anúncios e promoções entre a prévia e a análise completa, com isolamento por cliente, loja e conta.
- Permite analisar o SKU aberto junto da lista sem ocupar as vagas reservadas às leituras locais.
- Preserva análises de outros SKUs durante a gravação e rejeita respostas superadas por mudanças nos dados.
- Aproveita o frete zero confirmado e interrompe tentativas em cascata quando o provedor está indisponível.

O `app-update` reutiliza o runtime compatível existente. O Setup completo acompanha a mesma release.
