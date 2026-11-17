# JK Sistema 1.0.360

Atualização pelo canal `app-update` com planejamento de compras por produto em Médias e Pedidos.

- A sugestão compara os três últimos meses completos de cada SKU com os três anteriores e verifica o movimento dentro dos três meses recentes. Sinais divergentes ou estoque atual zerado exigem revisão antes de reduzir automaticamente a previsão. Histórico curto e vendas esporádicas também ficam sinalizados para revisão.
- O planejamento considera seis meses até o recebimento, verifica a reserva de um mês antes da entrega e calcula a quantidade para completar sete meses de estoque no total após receber. A previsão de falta anterior à chegada permanece explícita e não aumenta o novo pedido para recomprar vendas perdidas.
- Compras confirmadas entram na previsão por data e quantidade ainda não recebida. Orçamentos em análise não garantem abastecimento; recebimentos sem data válida exigem revisão.
- A coluna Sugestão de compra mostra a quantidade, a tendência e as setas de alta em verde ou de queda em vermelho. Os detalhes de estoque antes da entrega, reserva e alertas aparecem ao passar o mouse ou focar a quantidade.
- A tabela, a geração do pedido e o download da sugestão usam o mesmo cálculo. Ajustes manuais de quantidade continuam disponíveis.
- A leitura preserva o saldo zero da loja e prioriza sua identidade sobre nomes iguais e registros legados duplicados.

Os arquivos de versão, locks, contratos de versão e manifesto do bundle de contexto acompanham a versão 1.0.360. O contrato do runtime permanece igual; o pacote Update reutiliza o runtime compatível existente. O Setup completo acompanha a release conforme o workflow de distribuição.

Validação local: 1.177 testes Python de Médias e Pedidos aprovados, com um teste de link ignorado pela permissão do ambiente; seis testes de distribuição e versão; oito verificações de perfis de release, materialização, bootstrap, staging, extração WiX, contrato de runtime e configuração do Update aprovados. As regressões de navegador conferem o balão real por mouse e teclado, fechamento com Escape, alternância entre quantidades, texto seguro, celular, edição e virtualização com até 2.000 produtos. Locks de dependências, manifesto do bundle de contexto, espelhos HTML, compilação Python, sintaxe JavaScript e `git diff --check` conferidos.
