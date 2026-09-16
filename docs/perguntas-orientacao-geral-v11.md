# Orientação de Perguntas v11: identidade da peça e compatibilidade

A v11 mantém as regras públicas da v10 e corrige a interpretação de anúncios com atributos técnicos contraditórios. Aplica-se à política compartilhada, ao planejamento técnico, à resolução, à redação e à revisão factual. O pós-venda preserva sua política própria.

## Autoridade das fontes

A API oficial do Mercado Livre comprova o conteúdo e o estado atual do anúncio. Um atributo preenchido pelo vendedor não se transforma em confirmação técnica do fabricante porque foi obtido nessa API. Confronte título, atributos e descrição com o documento do SKU e as fontes técnicas, observando cliente, loja, seller, site, anúncio, SKU e variação exatos.

Compatibilidade exige duas ligações sustentadas: SKU/variação para a peça ou referência efetivamente vendida; e essa referência para o veículo ou equipamento consultado. Um catálogo OEM pode comprovar a segunda ligação sem comprovar a primeira. Fotografias, títulos semelhantes e respostas anteriores da loja, isoladamente, não comprovam a identidade ou a aplicação. Notas editoriais sem fonte também não substituem evidência.

Nas divergências, discrimine componente, posição, lado, cor e variação de cada código. Uma descrição que mistura referências não demonstra equivalência entre todas elas. Quando o conjunto de evidências resolve a divergência e sustenta as duas ligações, a resposta deve aproveitar a conclusão. Se a identidade da peça continuar incerta, mantenha a insuficiência; pesquisa vazia ou timeout não confirmam aplicação nem incompatibilidade.

## Atendimento

Informe primeiro a aplicação comprovada. Quando faltar somente identificar a geração ou carroceria do veículo do comprador, apresente a condição e peça apenas geração ou ano/carroceria ainda ausentes. Aproveite esses dados se já constarem do histórico. Não exija código original quando esses dados resolvem a dúvida e não invente faixas anuais para substituir gerações comprovadas.

Não exponha o conflito do cadastro ao comprador. Uma resposta condicional não incentiva a compra nem usa urgência. Preserve a assinatura configurada exatamente uma vez e os limites atuais de perguntas públicas, incluindo a proibição de solicitar VIN/chassi, fotos e anexos.

Quando faltar identificar internamente a mercadoria, não transfira ao comprador a conferência do estoque. Se nenhum dado dele resolver essa lacuna, informe que a aplicação ainda não pode ser confirmada, preserve os demais fatos conhecidos e não invente uma pergunta.

## Contratos e verificação

`ml_questions_gemini/public_reply_policy.py` contém a orientação compartilhada. A política completa é `jk_ppv_response_policy_v11`; o hash do orquestrador inclui `public-reply-evidence-guidance-v2` e `sku-reference-fitment-links-v1`. Rascunhos da política anterior deixam de ser aprováveis pelo contrato existente. Versões de schema, formato JSON, rotas e fluxo de aprovação foram preservados.

As verificações de integração cobrem os caminhos de contexto integral e legado, prompts de contingência, assinatura, pós-venda, dados não confiáveis, estado condicional sem CTA e invalidação de rascunhos v10. A avaliação de geração deve usar contexto sintético e conferir: geração ausente; geração já conhecida no histórico; conflito resolvido; vínculo SKU/OEM ausente; pesquisa vazia ou timeout; tentativa de instrução no anúncio; e fontes de outra loja.
