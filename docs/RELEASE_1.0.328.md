# JK Sistema 1.0.328

## Perguntas

- **Gerar IA** mostra qual consulta falhou e informa se houve erro, tempo esgotado, indisponibilidade ou ausência de resultado. Quando os dados confirmados sustentam a resposta, o sistema gera um rascunho para revisão. Se faltarem dados, mantém o texto que você já digitou.
- **Responder** mostra o motivo confirmado quando o Mercado Livre exige um anúncio ativo, como uma pausa por falta de estoque. O rascunho fica disponível para continuar a edição.

## Vendas

- A sincronização automática inclui o dia atual no primeiro ciclo disponível após a meia-noite no horário de Brasília, inclusive em planos que já estavam ativos.
- A fila retoma os dias pendentes e reaproveita os dias já confirmados.

## Médias e Pedidos

- A tabela aparece antes das fotos. As miniaturas são buscadas em lotes conforme as linhas dos SKUs são exibidas.
- O sistema reutiliza dados de vendas, estoque, trânsito e cadastro que continuam válidos. Uma mudança apenas no estoque pode atualizar o saldo e as sugestões de compra sem refazer todo o cálculo.

## Instalação

- O atualizador automático usa o pacote **Update** quando o ambiente local já está validado.
- Para instalar em outro computador ou reparar o ambiente Python, use o **Setup completo**.
