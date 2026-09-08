# Conhecimento integral por loja e SKU V18

Este contrato vale somente para perguntas públicas do Mercado Livre. Pós-venda
e os demais consumidores do Context Hub mantêm o comportamento existente.

## Fluxo

```text
Obsidian por loja
  -> compilação e publicação isolada no Context Hub
  -> vínculo exato item/variação/SKU
  -> envelope canônico integral e imutável
  -> etapas da IA
```

A IA não lê o vault diretamente. O servidor lê a geração ativa da loja e
materializa o contrato fechado `jk_ml_store_sku_question_context_v1`. Não há
busca textual nem fallback para documento genérico, geração global, outra loja
ou outro SKU.

## Escopos iniciais

| Loja | store_id | seller | site |
| --- | --- | --- | --- |
| JK Peças | `b1e5a6efb16c0db69bba1836` | `588182191` | `MLB` |
| Carlos José | `666955b171470f4fb6f6a4a8` | `1275608482` | `MLB` |
| Uai Mineirinho | `22bb88ce93b504a26dcef2b5` | `375033834` | `MLB` |

Cada loja possui documentos, hashes, bindings, orientações, geração ativa e
rollback próprios. Usuários autorizados da mesma loja compartilham essa base;
autor, revisor e aprovador são persistidos somente como identificadores opacos.

## Contratos e estrutura

- Curadoria `context_schema: 3` exige `tenant_scope`, `store_ref`, `seller_id`,
  `site_id`, `scope_kind`, `sku`, `knowledge_role`, `surface`, hashes e estado.
- `jk_context_store_sku_binding_v1` vincula um item e sua variação ao SKU.
- `jk_context_store_sku_migration_v1` registra prévia, hash, estado e geração.
- `jk_ml_store_sku_question_context_v1` é o envelope integral enviado à IA.

O vault usa:

```text
70_Gerado/Lojas/<store_id--loja>/SKUs/<sku>/Contexto.md
80_Curadoria/Lojas/<store_id--loja>/Orientacoes-Gerais.md
80_Curadoria/Lojas/<store_id--loja>/SKUs/<sku>/Orientacoes.md
90_Arquivo/Quarentena/SKUs-sem-identidade/
```

O SKU legado `1599` é bloqueado pelo contrato e permanece em quarentena. Uma
orientação de SKU só pode ser criada ou publicada quando o SKU existe na geração
ativa exata da loja.

## Conteúdo enviado

O envelope preserva todos os campos originais do documento canônico do SKU,
todas as orientações gerais da loja, as orientações daquele SKU, identidade,
hashes e estado de validade da geração, lacunas/conflitos e somente os dados
operacionais pedidos.
Preço, estoque, promoção, frete e prazo continuam vindo das fontes atuais do
Mercado Livre/Bling e não são gravados no vault.

Frontmatter, índices de navegação, caminhos locais, relações derivadas repetidas
e conteúdo de outro escopo não entram no envelope. Isso não remove informação
canônica do documento.

Os limites validam o conteúdo e nunca o cortam:

| Parte | Limite de caracteres |
| --- | ---: |
| Documento canônico | 24.000 |
| Orientações aplicáveis | 4.000 |
| Dados operacionais | 2.000 |
| Pergunta e histórico relevante | 1.500 |
| Envelope integral | 32.000 |
| Prompt simples, incluindo envelope | 40.000 |
| Etapa técnica, incluindo envelope | 48.000 |
| Proteção de transporte | 52.000 |

Se qualquer limite for excedido, a automação é bloqueada e encaminhada para
revisão; nenhuma evidência é truncada silenciosamente. Classificadores e
validações determinísticas não recebem o documento. Todas as gerações técnicas
recebem o mesmo envelope como um único resultado tipado.

## Curadoria e migração

Orientações salvas pela interface entram como rascunho schema 3 no Obsidian.
Elas só substituem a orientação ativa depois de validação, revisão, aprovação e
publicação explícita. O JSON legado é somente uma fonte de migração.

A migração possui modo de prévia sem escrita, valida catálogo completo e
variações, calcula hashes, publica cada loja em transação própria, permite
reexecução idempotente e registra a geração anterior para rollback por loja.
Seller/site divergente, associação ambígua, catálogo incompleto ou SKU sem
binding bloqueiam a associação. A aplicação sobre dados reais exige autorização
separada.

## Versionamento e empacotamento

- Prompt: `jk_ml_customer_reply_codex_v18`.
- Resposta pública: schema `5.2`, sem alteração de assinatura.
- Pesquisa adaptativa: `jk_black_jhon_research_v3`.
- Compilador, contratos, repositório e migração são módulos separados.

Os seis módulos V18, incluindo os auxiliares de transação e materialização, são
arquivos críticos no manifesto de verificação do
instalador. Uma futura build falhará se eles não forem copiados para o pacote.
O manifesto continua proibindo dados de tenant, vaults, bancos e catálogos SKU
dentro do instalador.
