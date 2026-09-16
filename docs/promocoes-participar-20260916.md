# Decisão de participação em promoções

## Comportamento

A análise calcula a sugestão inicial; a coluna Ação guarda a decisão efetiva. Normalizar os dados, mudar de aba, paginar, configurar colunas ou alterar os parâmetros da próxima análise não substitui essa decisão. Uma análise nova inicializa novas sugestões.

A confirmação humana processa todas as linhas marcadas como Participar. O cartão e o payload usam a mesma seleção. A flag financeira permanece na recomendação e na automação sem aprovação humana; não retira escolhas humanas da operação. Dados financeiros insuficientes aparecem junto da sugestão Não participar.

Análises são vinculadas ao cliente e à loja de origem. Respostas antigas de análise não podem substituir resultados de outro contexto, nem uma confirmação aberta pode atravessar uma troca de contexto. Alterar a seleção invalida a marca Confirmada daquela campanha.

## Contrato de execução

As rotas e os schemas existentes permanecem compatíveis. Baseline de blobs Git no commit `bbe4d2132d9d02475e143fe4a71ca607fe0222d4`:

- `backend/schemas/promocoes.py`: `4630efc67e6ed683f974233fed7e573c63966aa9`.
- `backend/routers/promocoes.py`: `21f20513bfd9a829c4240e62d1ac3e89084eec6c`.

Cada item recebe `client_ref` no cliente; clientes anteriores recebem referência por índice no servidor. `action_promotion_id`, quando informado, precisa corresponder à campanha do grupo. Dados de execução da análise têm precedência exclusiva sobre preços/ofertas de exibição; ausência não autoriza substituir termos por outra campanha.

Cada entrada recebe exatamente um detalhe. O campo aditivo `outcome` distingue:

| Estado | Significado | Contador legado |
| --- | --- | --- |
| applied | Inclusão confirmada | total_sucesso |
| already_participating | Participação existente comprovada | total_ignorados |
| blocked | Dados indispensáveis ausentes ou divergentes | total_falha |
| rejected | Recusa explícita | total_falha |
| unknown | Sem confirmação suficiente | total_falha |

`total_itens = total_sucesso + total_ignorados + total_falha`. O detalhe contém `client_ref`, campanha, anúncio, `success`, `ignored`, `status`, `message` e, nas falhas, `error`. Todos os detalhes são retornados; a interface apresenta páginas de 50 entradas por categoria. Resultado ausente ou ambíguo não equivale a confirmação. A campanha só recebe Confirmada quando toda a seleção correspondente está aplicada ou já participa.

A consulta preliminar inconclusiva permite tentar a adesão quando os requisitos técnicos estão presentes. Falta de oferta obrigatória pode ser resolvida por consulta à mesma campanha/anúncio. Recusa não autoriza preço alternativo. Timeout após envio não produz confirmação nem reenvio automático.

## Validação

Regressões cobrem seleção de 236 itens, preservação de escolhas e sugestões, automação financeira, navegação, parâmetros da próxima análise, identidade de contexto, resultados completos acima de 300 itens, paginação no navegador, dados faltantes, respostas ambíguas, rejeições, falhas de transporte e termos aprovados.

Todos os cenários de participação usam dados sintéticos e chamadas simuladas. A reprodução 236/11 demonstra a falha anterior; não constitui auditoria individual dos anúncios das imagens.

Validação concluída: 247 testes Python e 22 subtestes aprovados, 10 arquivos de testes JavaScript aprovados (incluindo Chromium), compilação Python e `git diff --check` aprovados. Os testes de compatibilidade dos HTML espelhados e do contrato de efetivação de Favoritos também passaram. Há somente um aviso preexistente de depreciação do Pydantic nos testes de cupons.

Entrega em worktree e commit para revisão. Integração e publicação seguem aprovações próprias do projeto.
