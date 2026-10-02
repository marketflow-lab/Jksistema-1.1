# JK Sistema 1.0.159

Data: 2 de outubro de 2026.

- Corrige a pesquisa de anuncios da mesma loja quando a pergunta pede outra voltagem ou uma alternativa do produto.
- A pesquisa utiliza a consulta solicitada pela IA, preserva a identidade da loja e do vendedor e verifica os anuncios retornados antes de fornecer evidencia.
- Variacoes do anuncio atual podem participar da pesquisa. Uma consulta sem resultados permite continuar a analise sem afirmar que o produto nao existe.
- Falhas de API, timeout ou resultados incompletos permanecem identificados como pesquisa indisponivel.
- Invalida o contrato anterior da IA para impedir o reaproveitamento do fluxo defeituoso.
- Distribuicao pequena por `app-update`, preservando os pins de runtime e dependencias da versao 1.0.158.
