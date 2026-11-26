# JK Sistema 1.0.368

Atualização pequena pelo canal `app-update`.

- A Pesquisa de Mercado coleta vendas, data de criação e média mensal do Avant Pro no perfil dedicado do Chrome. O JK confirma o anúncio e o vendedor antes de usar as métricas no ranking. O fluxo de recuperação reconsulta anúncios com métricas antigas.
- A Pesquisa de Mercado salva os links oficiais das categorias após confirmar o ID. Ao reutilizar um link, o JK verifica o ID novamente antes de coletar os anúncios.
- O Chrome abre cada aba auxiliar quando há um anúncio a consultar. O JK reaproveita a prévia de **Mostrar navegador** e fecha a aba vazia criada quando a navegação falha antes de carregar.
- Médias e Pedidos revalida as vendas quando os dados mudam durante o cálculo. O histórico de meses fechados é salvo por loja e reaproveitado nas próximas consultas.
- A média, a cobertura e a sugestão de compra consideram os meses exibidos, incluindo o mês atual e meses sem vendas.
- A tabela de Médias e Pedidos preserva a posição da rolagem ao editar ou ocultar um SKU.
