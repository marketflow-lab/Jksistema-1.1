# Margem da campanha e recomendação

## Correção

A promoção 2 passa a apresentar o cenário financeiro da campanha selecionada, o mesmo que alimenta a sugestão. Uma oferta ativa de outra campanha não fornece os preços, tarifas ou margens exibidos nessa comparação. O cálculo mantém a confirmação de campanha, tipo e anúncio e as consultas da loja e cliente de origem.

Falta de identificador da oferta ou situação que exige verificação são pendências técnicas de envio. Elas não tornam uma margem calculada em falta de dados financeiros. Uma margem válida pode recomendar Participar mesmo com pendência técnica, que aparece separadamente e continua visível após uma escolha manual.

Quando faltam custo, imposto, tarifa ou frete no preço da campanha, a análise informa o motivo financeiro. Não apresenta como válida uma margem calculada usando outra oferta. O exportador preserva o resultado canônico da análise, sem gerar uma segunda margem a partir de valores arredondados.

## Compatibilidade e operação

Campos aditivos: `action_financeiro_motivo`, `action_impedimento_tecnico` e `action_tecnico_apto`. As rotas, schemas públicos e versão do aplicativo permanecem iguais ao commit base `2974238ad3dc27efd110c6d2141dfc8def288e3e`.

A operação humana mantém todos os selecionados e retorna um resultado por anúncio. A automação exige finanças confirmadas e aptidão técnica. Campos `action_*` têm precedência exclusiva também na automação do servidor; valores ausentes não são completados com preços ou ofertas de outro cenário.

Escolhas manuais, parâmetros aplicados na próxima análise, resultados explícitos e ausência de preço alternativo permanecem como na correção anterior. Resultados de análises antigas devem ser substituídos por uma nova análise para receber o cálculo e os diagnósticos corrigidos.

## Verificação

Os testes usam dados sintéticos e chamadas simuladas, sem enviar operações ao Mercado Livre. Cobrem margem calculada sem oferta, divergência entre campanha ativa e selecionada, dados financeiros ausentes, preservação do cálculo na exportação, isolamento de contexto, escolha humana e automação. Testes no navegador verificam o aviso técnico separado, sua persistência após escolha manual e renderização de motivos como texto.

Validação concluída: 219 testes do conjunto de promoções e 49 subtestes, mais 101 testes de módulos relacionados, todos aprovados. Os 11 arquivos de testes JavaScript passaram, incluindo os dois testes no Chromium. Compilação Python e `git diff --check` aprovados. Apenas o aviso preexistente de depreciação do Pydantic permaneceu.

Entrega isolada para revisão; incorporação ao desenvolvimento depende da aprovação do novo commit.
