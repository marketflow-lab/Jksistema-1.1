# JK Sistema 1.0.346

Atualização pelo canal `app-update` com as correções aprovadas de Médias, Vendas e fotos dos produtos.

- Médias conserva o histórico de vendas em blocos compactos para reduzir as releituras durante a sincronização e a atualização dos períodos.
- A renovação das credenciais de uma loja preserva o cálculo em andamento quando a identidade, as permissões e os dados consultados continuam iguais.
- Vendas reaproveita a normalização dos SKUs e dos dados de Pareto, com menos conversões repetidas no resumo e no gráfico.
- As fotos em Médias recebem um prazo maior e novas tentativas limitadas após falhas temporárias, preservando loja, cliente, sessão e política de acesso.
- Requisições idênticas de fotos compartilham o trabalho em andamento, evitando leituras duplicadas após uma desconexão.

O `app-update` reutiliza o runtime compatível já instalado. O Setup completo continua disponível na mesma release.
