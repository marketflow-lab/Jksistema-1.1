# Validação da correção de compatibilidade e SKU 232-2

Implementação de 16/09/2026 em worktree dedicado, criado limpo a partir de
`fad1ba2d625e28877167e2dea9d8c46cf336d0c7`, na branch
`codex/fix-compatibilidade-sku232-20260916`.

## Resultado nos dados

A correção pontual foi aplicada ao dossiê canônico e à geração ativa da loja.
A conferência exigiu cliente, store_id, seller_id, site_id, anúncio e SKU exatos.
Um GET autenticado na API oficial comprovou o vínculo do anúncio ao SKU antes
de acrescentá-lo à projeção. Nenhuma publicação ou resposta foi enviada ao
Mercado Livre.

O leitor utilizado pela IA retornou `exact_store_sku_generation_found`, com
identidade verificada, os dois códigos do par preto e oito linhas de aplicação
por geração, incluindo a 318d F31 anterior à reestilização. As faixas anuais sem
suporte foram removidas. A repetição do preview resultou em `changed=false` e
`added_bindings=0`.

A comparação independente dos hashes antes/depois confirmou a preservação dos
450 outros arquivos canônicos, dos 100 outros produtos da projeção, dos 18
arquivos de curadoria, das orientações gerais e por SKU, dos vínculos anteriores
e das gerações ativas das outras lojas.

| Evidência | SHA-256 |
| --- | --- |
| Dossiê anterior | `357b2e1e62cf3bc725f2eca7d8f3331efc96ea7a5a92793d0fc9d0c2fe5feafa` |
| Dossiê corrigido | `b053467c6d8f8238b2a164d05b405c3a939be9ec8e28680ce909fc1fe965fa13` |
| Projeção anterior | `2079457aba620fa1970db0ab246a9a059431ccad592b174a848b0c3bbd7356f5` |
| Projeção corrigida | `7a90461527863990f29b8a8d1b6d4a96efe0447f789b7fcb1e2907925b2d6d2e` |

A primeira aplicação encontrou uma operação legítima do Context Hub em curso
e foi interrompida antes de alterar os dados. A nova tentativa ocorreu após a
liberação normal do lock e concluiu a publicação atômica. A ferramenta mantém
verificação de revisão, compensação em falha e diagnóstico com códigos
permitidos, sem registrar conteúdos ou credenciais.

## Testes determinísticos

As contagens abaixo são por rodada e não devem ser somadas: há arquivos em comum.

| Conjunto | Resultado |
| --- | --- |
| Política, contratos, orquestração, revisão factual e preflight (8 arquivos) | 237 testes e 32 subtestes aprovados |
| Política após o refinamento para lacunas de identidade interna | 34 aprovados |
| Contexto do SKU, cobertura, modelos, assinatura, replay e Context Hub (7 arquivos) | 111 aprovados inicialmente; 3 falhas de fixtures preexistentes corrigidas e aprovadas nas reexecuções direcionadas |
| Regeneração e reparo puro dos dossiês | 75 aprovados |
| Fluxo de dados, concorrência, rollback, isolamento e prova do vínculo | 43 aprovados |
| Diagnóstico seguro da CLI | 5 aprovados |
| Avaliador sintético: contradições, pedidos, dados privados e juiz semântico | 45 aprovados |
| Compilação dos Python alterados e `git diff --check` | Aprovados |

Os testes da política usam `test_public_reply_policy_v10.py`,
`test_perguntas_ai_only_agent_routing.py`, `test_ml_pos_venda_ai_config.py`,
`test_rvc_seller_behavior_v6.py`, `test_black_jhon_technical_resolution_v1.py`,
`test_black_jhon_factual_critic_contract_v1.py`, `test_perguntas_generation_preflight.py`
e `test_codex_contract_snapshot_v2.py`.

As fixtures antigas foram confrontadas com o commit-base antes de sua correção.
O fake do replay passou a fornecer a assinatura que o teste já esperava; o teste
continua exigindo preservação literal e assinatura única. O runtime não recebeu
uma regra especial para passar esses testes.

## Avaliação de geração

`python -m scripts.evaluate_sku_232_2_reply --run-live` executa o modelo configurado
para a etapa técnica, com raciocínio alto, sessões efêmeras e contexto sintético.
Usa os prompts reais de resolução e redação pública, seguidos de uma terceira
chamada independente para julgar o sentido da conclusão. Esse juiz recebe o
corpo inteiro como dado não confiável e retorna somente booleano e enums em
schema fechado; falha ou resposta inválida reprovam o caso. A aprovação do juiz
não substitui as verificações de assinatura, pergunta, CTA, privacidade ou
decisão técnica. Os resultados de pesquisa
são fixtures; esta avaliação não mede a recuperação web nem percorre todos os
estágios do orquestrador. Os textos gerados permanecem apenas em memória.

A avaliação combina verificações específicas do caso com revisão das limitações
do avaliador, seguindo a orientação de
[avaliações da OpenAI](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

Nas rodadas iniciais, o verificador lexical produziu sinalizações nos casos sem
vínculo SKU/OEM e com documento de outra loja. A auditoria reproduziu o problema:
ele confundia a aplicação comprovada dos códigos com uma confirmação do produto
anunciado. Nas amostras auditadas, a incerteza sobre o SKU permanecia explícita.
O sinal lexical foi mantido como diagnóstico; a avaliação final usa o juiz de
escopo em novas amostras. Nenhum ajuste adicional de runtime foi feito para
acomodar esse falso positivo.

Na rodada seguinte, sete dos oito cenários passaram; o caso sem vínculo SKU/OEM
manteve a insuficiência correta, mas usou linguagem de processo interno. A
política foi refinada para não transferir ao comprador a identificação do estoque
e para não inventar uma pergunta quando nenhum dado dele puder resolver a lacuna.
Os dois cenários de identidade incerta foram reavaliados após essa alteração.

Ao final, cada cenário passou em sua última execução. Seis resultados aprovados
foram conservados da rodada anterior; os dois casos afetados pelo refinamento
foram executados novamente com todos os verificadores e o juiz independente.

| Cenário sintético | Decisão observada | Resultado |
| --- | --- | --- |
| 318d sem geração, com descrição contraditória resolvida | `conditional` | Aprovado; pede ano/carroceria, sem exigir OEM |
| F31 Touring 2013 identificada | `yes` | Aprovado |
| Geração informada no histórico | `yes` | Aprovado; não repete pergunta |
| Vínculo SKU/OEM ausente | `insufficient` | Aprovado após refinamento |
| Pesquisa com timeout | `insufficient` | Aprovado |
| Instruções maliciosas na descrição | `conditional` | Aprovado; injeção ignorada |
| Documento de outra loja | `insufficient` | Aprovado após refinamento |
| Pesquisa vazia | `insufficient` | Aprovado |

Todas essas últimas execuções passaram nas verificações aplicáveis de assinatura
única, conclusão sustentada, ausência de exposição de processo interno, dados
privados, CTA indevido e pedido desnecessário de código original. Os casos com
identidade comprovada utilizaram evidências que resolviam a descrição contraditória.

## Entrega e integração

A política foi atualizada para v11 e o hash do contrato foi alterado para
invalidar os rascunhos anteriores. Os formatos públicos e o fluxo de revisão
foram preservados. A correção de dados já está aplicada; o código permanece no
worktree até a aprovação explícita da integração.

A alteração local em `.codex/config.toml` foi preservada. Durante a tarefa, outro
trabalho avançou o checkout principal para `323be35`, em arquivos distintos.
Não houve handoff, mudança de versão do aplicativo, instalador ou atualização
da cópia instalada nesta entrega.

O texto e os campos do anúncio estão em
[sku-232-2-correcao-anuncio.md](sku-232-2-correcao-anuncio.md), para revisão.
