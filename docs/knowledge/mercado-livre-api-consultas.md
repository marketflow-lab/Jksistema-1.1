---
truth_class: versioned_technical
---

# Mercado Livre — orientação de busca na API

Versão do catálogo: **2026-07-20.1**
Documentação conferida em: **20/07/2026**
Escopo: endpoints de consulta encontrados na documentação oficial brasileira do Mercado Livre.

## Para que serve

Este documento orienta o Codex interno do JK Sistema — João Pretinho/Black Jhon — a localizar e consultar dados do Mercado Livre sem receber URL livre, token, seller_id ou caminho arbitrário. O desenvolvimento do código continua sendo responsabilidade do **Codex Desktop em Worktree**.

A documentação oficial é dinâmica. Este arquivo é um índice versionado: ele não copia integralmente o conteúdo do portal, mas registra cada rota de consulta, sua política interna e um link direto para a página oficial que deve ser relida antes de uma mudança de contrato.

## Como pesquisar

Use a ferramenta `mercado_livre_resource_query`.

- Sem `resource_id`: pesquisa o catálogo por termos como “estoque”, “billing”, “visitas”, “envio” ou “reclamação”.
- Com `resource_id`: executa somente a rota fixa correspondente, quando a política permitir.
- `loja`: obrigatória e exata para qualquer execução.
- `params`: objeto fechado; somente os campos publicados no catálogo são aceitos.
- Nunca forneça URL, token, `seller_id`, cabeçalhos ou método HTTP.

Exemplo seguro:

```json
{
  "resource_id": "ml.stock.user_product",
  "loja": "Nome exato da loja",
  "params": {
    "user_product_id": "MLBU123456"
  },
  "limite": 50
}
```

O catálogo completo também fica disponível, somente para administrador, em `GET /api/admin/codex/assistant/mercado-livre/resources`.

## Políticas de execução

| Política | Significado |
|---|---|
| `allowlisted` | GET de consulta liberado com caminho e parâmetros fechados. |
| `allowlisted_redacted` | GET liberado, mas a resposta passa por remoção reforçada de dados pessoais, segredos e campos privados. |
| `delegated` | A rota usa uma função especializada já existente no JK Sistema. |
| `document_only` | Rota conhecida, mas não executável pelo assistente interno; inclui binários, POSTs consultivos, escopos de maior risco e recursos ainda sem rollout. |
| `tombstone` | Endpoint removido; mantido apenas para impedir uso acidental. |

Todas as rotas executáveis são somente leitura. POSTs de validação/diagnóstico que a documentação apresenta como consulta permanecem `document_only` nesta versão. Rotas de criação, alteração, resposta, cancelamento, etiqueta, download privado ou mutação não fazem parte do executor.

## Proteções obrigatórias

- O servidor injeta o usuário vendedor e o site obtidos do OAuth da loja.
- Recursos de anúncio, User Product, família, pedido, envio e reclamação exigem confirmação de propriedade.
- Billing info é derivado de um pedido pertencente à loja; o modelo não informa `billing_info_id`.
- Conversas pós-venda usam `mark_as_read=false`.
- Respostas HTTP 206 são marcadas como parciais; 401 pede reconexão; 429 pede nova tentativa posterior.
- A consulta de estoque de User Product preserva apenas o header de resposta `x-version`; ele não é exigido no GET e serviria somente para um PUT futuro, que continua bloqueado.
- Listas, paginação, datas, tamanho da resposta e tempo total têm limites.
- Logs e respostas não carregam token, autorização, CPF/CNPJ, e-mail, telefone, endereço, dados do comprador, anexos privados ou conteúdo bruto sensível.
- Não existe fallback para outra loja quando a loja exata não comprova a propriedade.

## Limitações conhecidas da API oficial

- A documentação não oferece um endpoint oficial para enumerar todas as famílias de User Product de um vendedor; é necessário partir de item, SKU ou User Product conhecido.
- `POST /catalog_compatibilities/products_search/chunks` foi removido em 15/07/2026 e está registrado somente como tombstone.
- Product Ads está catalogado, mas continua bloqueado até rollout específico de permissões e métricas.
- Downloads binários de etiquetas, anexos, XML, documentos legais e relatórios permanecem fora do assistente.
- Alguns verticais ainda apresentam diferenças entre `/health` e `/performance`; o catálogo atual usa as rotas de performance confirmadas para produto comum.

## Catálogo por domínio

Legenda de propriedade: `public` (dado público), `site` (site da conta), `seller` (vendedor da loja), `token_scoped` (escopo do OAuth), `owned_*` (propriedade comprovada) e `billing_from_order` (derivado de pedido comprovado).

### Usuários e conta

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.users.me` | `GET /users/me` | `allowlisted_redacted` | `seller` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/servico-consulta-de-usuarios) |
| `ml.users.get` | `GET /users/{user_id}` | `allowlisted_redacted` | `seller` | user_id | [oficial](https://developers.mercadolivre.com.br/pt_br/servico-consulta-de-usuarios) |
| `ml.users.addresses` | `GET /users/{user_id}/addresses` | `document_only` | `seller` | user_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/enderecos-do-usuario) |
| `ml.users.blocked_by_questions` | `GET /block-api/search/users/{buyer_id}` | `document_only` | `token_scoped` | buyer_id; fixos: type=blocked_by_questions | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.users.blocked_by_order` | `GET /block-api/search/users/{buyer_id}` | `document_only` | `token_scoped` | buyer_id; fixos: type=blocked_by_order | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.users.reputation_recovery_status` | `GET /users/reputation/seller_recovery/status` | `allowlisted_redacted` | `seller` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/recuperacao-de-reputacao) |
| `ml.users.reputation_recovery_legal_document` | `GET /users/reputation/seller_recovery/legal-document` | `document_only` | `seller` | type | [oficial](https://developers.mercadolivre.com.br/pt_br/recuperacao-de-reputacao) |

### Comunicações

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.communications.notices` | `GET /communications/notices` | `allowlisted_redacted` | `token_scoped` | limit, offset | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-receba-notificacoes) |

### Notificações

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.notifications.missed_feeds` | `GET /missed_feeds` | `document_only` | `token_scoped` | app_id, topic, limit, offset | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-receba-notificacoes) |

### Sites, categorias e catálogo

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.sites.list` | `GET /sites` | `allowlisted` | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.sites.listing_types` | `GET /sites/{site_id}/listing_types` | `allowlisted` | `site` | site_id | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.sites.listing_exposures` | `GET /sites/{site_id}/listing_exposures` | `allowlisted` | `site` | site_id | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.categories.roots` | `GET /sites/{site_id}/categories` | `allowlisted` | `site` | site_id | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.categories.dump` | `GET /sites/{site_id}/categories/all` | `document_only` | `site` | site_id | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.categories.get` | `GET /categories/{category_id}` | `allowlisted` | `public` | category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.categories.predict` | `GET /sites/{site_id}/domain_discovery/search` | `allowlisted` | `site` | site_id, q, limit, target | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.categories.attributes` | `GET /categories/{category_id}/attributes` | `allowlisted` | `public` | category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/atributos) |
| `ml.categories.techspec_input` | `GET /categories/{category_id}/technical_specs/input` | `allowlisted` | `public` | category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/atributos) |
| `ml.categories.techspec_output` | `GET /categories/{category_id}/technical_specs/output` | `allowlisted` | `public` | category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/atributos) |
| `ml.categories.conditional_attributes` | `POST /categories/{category_id}/attributes/conditional` | `document_only` | `public` | category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/atributos) |
| `ml.products.search` | `GET /products/search` | `allowlisted` | `public` | site_id, q, domain_id, product_identifier, parent_product_id, status, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/buscador-de-produtos) |
| `ml.products.get` | `GET /products/{product_id}` | `allowlisted` | `public` | product_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/buscador-de-produtos) |

### Localização e moedas

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.locations.countries` | `GET /classified_locations/countries` | `allowlisted` | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.locations.country` | `GET /classified_locations/countries/{country_id}` | `allowlisted` | `public` | country_id | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.locations.state` | `GET /classified_locations/states/{state_id}` | `allowlisted` | `public` | state_id | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.locations.city` | `GET /classified_locations/cities/{city_id}` | `allowlisted` | `public` | city_id | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.currencies.list` | `GET /currencies` | `allowlisted` | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.currencies.get` | `GET /currencies/{currency_id}` | `allowlisted` | `public` | currency_id | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.currencies.convert` | `GET /currency_conversions/search` | `allowlisted` | `public` | from, to | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.zip.get` | `GET /countries/{country_id}/zip_codes/{zip_code}` | `allowlisted` | `public` | country_id, zip_code | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |
| `ml.zip.range` | `GET /country/{country_id}/zip_codes/search_between` | `allowlisted` | `public` | country_id, zip_code_from, zip_code_to | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas) |

### Anúncios e publicações

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.items.public_search` | `GET /sites/{site_id}/search` | `allowlisted` | `site` | site_id, q, category, official_store_id, nickname, offset, limit, sort | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.items.seller_search` | `GET /users/{user_id}/items/search` | `allowlisted` | `seller` | user_id, status, category, sku, seller_sku, tags, user_product_id, has_compatibilities, orders, offset, limit, search_type, scroll_id, include_filters, missing_product_identifiers, sort | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.items.seller_search_restrictions` | `GET /users/{user_id}/items/search/restrictions` | `allowlisted_redacted` | `seller` | user_id | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.items.multiget` | `GET /items` | `allowlisted` | `public` | ids, attributes | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.items.get` | `GET /items/{item_id}` | `delegated` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.items.description` | `GET /items/{item_id}/description` | `delegated` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.items.available_listing_types` | `GET /items/{item_id}/available_listing_types` | `allowlisted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/tutorial-tipos-de-publicacao-y-atualizacao-de-artigos) |
| `ml.items.available_upgrades` | `GET /items/{item_id}/available_upgrades` | `allowlisted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/tutorial-tipos-de-publicacao-y-atualizacao-de-artigos) |
| `ml.items.available_downgrades` | `GET /items/{item_id}/available_downgrades` | `allowlisted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/tutorial-tipos-de-publicacao-y-atualizacao-de-artigos) |
| `ml.users.available_listing_types` | `GET /users/{user_id}/available_listing_types` | `allowlisted` | `seller` | user_id, category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/tutorial-tipos-de-publicacao-y-atualizacao-de-artigos) |
| `ml.users.available_listing_type` | `GET /users/{user_id}/available_listing_type/{listing_type_id}` | `allowlisted` | `seller` | user_id, listing_type_id, category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/tutorial-tipos-de-publicacao-y-atualizacao-de-artigos) |

### Preços e tarifas

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.items.prices` | `GET /items/{item_id}/prices` | `allowlisted_redacted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/precos-liquidos) |
| `ml.items.sale_price` | `GET /items/{item_id}/sale_price` | `allowlisted_redacted` | `owned_item` | item_id, context, quantity, destination_states | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/precos-liquidos) |
| `ml.items.price_to_win` | `GET /items/{item_id}/price_to_win` | `allowlisted_redacted` | `owned_item` | item_id; fixos: version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/precos-liquidos) |
| `ml.items.listing_prices` | `GET /sites/{site_id}/listing_prices` | `allowlisted` | `site` | site_id, price, category_id, listing_type_id, logistic_type, shipping_mode, free_shipping | [oficial](https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes) |
| `ml.suggestions.user_items` | `GET /suggestions/user/{user_id}/items` | `allowlisted_redacted` | `seller` | user_id, limit, offset | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/referencias-de-precos) |
| `ml.suggestions.item_details` | `GET /suggestions/items/{item_id}/details` | `allowlisted_redacted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/referencias-de-precos) |

### User Products

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.user_products.get` | `GET /user-products/{user_product_id}` | `allowlisted_redacted` | `owned_user_product` | user_product_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/user-products) |
| `ml.user_products.family` | `GET /sites/{site_id}/user-products-families/{family_id}` | `allowlisted_redacted` | `owned_family` | site_id, family_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/user-products) |
| `ml.user_products.uptin_validate` | `GET /items/{item_id}/user_product_listings/validate` | `allowlisted_redacted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/user-products) |

### Avaliações

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.reviews.item` | `GET /reviews/item/{item_id}` | `allowlisted` | `public` | item_id, catalog_product_id, limit, offset | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/buscador-de-produtos) |

### Moderações

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.pictures.errors` | `GET /pictures/{picture_id}/errors` | `allowlisted` | `public` | picture_id | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.pictures.diagnostic` | `POST /moderations/pictures/diagnostic` | `document_only` | `token_scoped` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.moderations.last` | `GET /moderations/last_moderation/{reference_id}` | `document_only` | `token_scoped` | reference_id | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |
| `ml.moderations.infractions` | `GET /moderations/infractions/{user_id}` | `allowlisted_redacted` | `seller` | user_id, limit, offset, sort | [oficial](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) |

### Qualidade

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.quality.item_performance` | `GET /item/{item_id}/performance` | `allowlisted_redacted` | `owned_item` | item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/como-comecar/qualidade-das-publicacoes) |
| `ml.quality.user_product_performance` | `GET /user-product/{user_product_id}/performance` | `allowlisted_redacted` | `owned_user_product` | user_product_id | [oficial](https://developers.mercadolivre.com.br/pt_br/como-comecar/qualidade-das-publicacoes) |

### Visitas, tendências e destaques

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.visits.items_total` | `GET /visits/items` | `allowlisted` | `public` | ids | [oficial](https://developers.mercadolivre.com.br/pt_br/recurso-visits) |
| `ml.visits.items_period` | `GET /items/visits` | `allowlisted` | `public` | ids, date_from, date_to | [oficial](https://developers.mercadolivre.com.br/pt_br/recurso-visits) |
| `ml.visits.user_period` | `GET /users/{user_id}/items_visits` | `allowlisted_redacted` | `seller` | user_id, date_from, date_to | [oficial](https://developers.mercadolivre.com.br/pt_br/recurso-visits) |
| `ml.visits.item_window` | `GET /items/{item_id}/visits/time_window` | `allowlisted_redacted` | `owned_item` | item_id, last, unit, ending | [oficial](https://developers.mercadolivre.com.br/pt_br/recurso-visits) |
| `ml.visits.user_window` | `GET /users/{user_id}/items_visits/time_window` | `allowlisted_redacted` | `seller` | user_id, last, unit, ending | [oficial](https://developers.mercadolivre.com.br/pt_br/recurso-visits) |
| `ml.trends.site` | `GET /trends/{site_id}` | `allowlisted` | `site` | site_id | [oficial](https://developers.mercadolivre.com.br/pt_br/relatorios-de-faturamento/tendencias) |
| `ml.trends.category` | `GET /trends/{site_id}/{category_id}` | `allowlisted` | `site` | site_id, category_id | [oficial](https://developers.mercadolivre.com.br/pt_br/relatorios-de-faturamento/tendencias) |
| `ml.highlights.category` | `GET /highlights/{site_id}/category/{category_id}` | `allowlisted` | `site` | site_id, category_id, attribute | [oficial](https://developers.mercadolivre.com.br/pt_br/escolha-tipo-de-servico/mais-vendidos-no-mercado-livre) |
| `ml.highlights.product` | `GET /highlights/{site_id}/product/{product_id}` | `allowlisted` | `site` | site_id, product_id | [oficial](https://developers.mercadolivre.com.br/pt_br/escolha-tipo-de-servico/mais-vendidos-no-mercado-livre) |
| `ml.highlights.item` | `GET /highlights/{site_id}/item/{item_id}` | `allowlisted` | `site` | site_id, item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/escolha-tipo-de-servico/mais-vendidos-no-mercado-livre) |

### Compatibilidades de autopeças

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.compat.item_list` | `GET /items/{item_id}/compatibilities` | `allowlisted_redacted` | `owned_item` | item_id, extended, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.item_get` | `GET /items/{item_id}/compatibilities/{compatibility_id}` | `allowlisted_redacted` | `owned_item` | item_id, compatibility_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.item_note` | `GET /items/{item_id}/compatibilities/{compatibility_id}/note` | `allowlisted_redacted` | `owned_item` | item_id, compatibility_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.user_product_list` | `GET /user-products/{user_product_id}/compatibilities` | `allowlisted_redacted` | `owned_user_product` | user_product_id, main_domain_id, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.restriction_values` | `GET /catalog_compatibilities/restrictions/values` | `allowlisted` | `public` | main_domain_id, secondary_domain_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.items_summary` | `POST /items/compatibilities_summary` | `document_only` | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.family_product_count` | `POST /catalog_compatibilities/products_search/count_family_products` | `document_only` | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.order_snapshot` | `GET /compats-snapshots/orders/{order_id}` | `document_only` | `owned_order` | order_id | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |
| `ml.compat.chunk_search_removed` | `POST /catalog_compatibilities/products_search/chunks` | `tombstone`; removido 2026-07-15 | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas) |

### Perguntas

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.questions.search` | `GET /questions/search` | `allowlisted_redacted` | `seller` | item_id, from, status, offset, limit, scroll_id, sort_fields, sort_types, search_type; fixos: api_version=4 | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.questions.received` | `GET /my/received_questions/search` | `allowlisted_redacted` | `seller` | item, from, status, offset, limit, sort_fields, sort_types, search_type; fixos: api_version=4 | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.questions.response_time` | `GET /users/{user_id}/questions/response_time` | `allowlisted_redacted` | `seller` | user_id | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.questions.get` | `GET /questions/{question_id}` | `document_only` | `token_scoped` | question_id; fixos: api_version=4 | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |

### Classificados

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.contacts.item_questions` | `GET /items/{item_id}/contacts/questions` | `document_only` | `owned_item` | item_id, date_from, date_to | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.contacts.user_questions` | `GET /users/{user_id}/contacts/questions` | `document_only` | `seller` | user_id, date_from, date_to | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.contacts.item_questions_window` | `GET /items/{item_id}/contacts/questions/time_window` | `document_only` | `owned_item` | item_id, last, unit, ending | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |
| `ml.contacts.user_questions_window` | `GET /users/{user_id}/contacts/questions/time_window` | `document_only` | `seller` | user_id, last, unit, ending | [oficial](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas) |

### Pedidos e packs

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.orders.get` | `GET /orders/{order_id}` | `allowlisted_redacted` | `owned_order` | order_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.orders.search` | `GET /orders/search` | `allowlisted_redacted` | `seller` | order.status, order.date_created.from, order.date_created.to, order.date_last_updated.from, order.date_last_updated.to, q, offset, limit, sort | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.packs.get` | `GET /packs/{pack_id}` | `allowlisted_redacted` | `owned_pack` | pack_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.orders.discounts` | `GET /orders/{order_id}/discounts` | `allowlisted_redacted` | `owned_order` | order_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.orders.product` | `GET /orders/{order_id}/product` | `allowlisted_redacted` | `owned_order` | order_id, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.orders.billing_info` | `GET /orders/billing-info/{site_id}/{billing_info_id}` | `allowlisted_redacted` | `billing_from_order` | site_id, billing_info_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/faturamento-billing-info) |
| `ml.orders.feedback` | `GET /orders/{order_id}/feedback` | `allowlisted_redacted` | `owned_order` | order_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.feedback.get` | `GET /feedback/{feedback_id}` | `document_only` | `token_scoped` | feedback_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.orders.notes` | `GET /orders/{order_id}/notes` | `allowlisted_redacted` | `owned_order` | order_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |

### Pagamentos

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.payments.collection` | `GET /collections/{payment_id}` | `document_only` | `token_scoped` | payment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |
| `ml.payment_methods.get` | `GET /sites/{site_id}/payment_methods/{method_id}` | `allowlisted` | `site` | site_id, method_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas) |

### Envios

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.shipments.get` | `GET /shipments/{shipment_id}` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.items` | `GET /shipments/{shipment_id}/items` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.payments` | `GET /shipments/{shipment_id}/payments` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.costs` | `GET /shipments/{shipment_id}/costs` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.delays` | `GET /shipments/{shipment_id}/delays` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.lead_time` | `GET /shipments/{shipment_id}/lead_time` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.carrier` | `GET /shipments/{shipment_id}/carrier` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.sla` | `GET /shipments/{shipment_id}/sla` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.history` | `GET /shipments/{shipment_id}/history` | `allowlisted_redacted` | `owned_shipment` | shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.statuses` | `GET /shipment_statuses` | `allowlisted` | `public` | — | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipments.labels` | `GET /shipment_labels` | `document_only` | `token_scoped` | shipment_ids, response_type | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.shipping.item_options` | `GET /items/{item_id}/shipping_options` | `document_only` | `owned_item` | item_id, zip_code | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |
| `ml.flex.assignment` | `GET /flex/sites/{site_id}/shipments/{shipment_id}/assignment/v2` | `document_only` | `owned_shipment` | site_id, shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios) |

### Fulfillment e estoque distribuído

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.fulfillment.stock` | `GET /inventories/{inventory_id}/stock/fulfillment` | `allowlisted_redacted` | `token_scoped` | inventory_id, include_attributes | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas/envios-fulfillment) |
| `ml.fulfillment.operations_search` | `GET /stock/fulfillment/operations/search` | `allowlisted_redacted` | `seller` | inventory_id, date_from, date_to, type, external_references.shipment_id, sort, limit, scroll_id | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas/envios-fulfillment) |
| `ml.fulfillment.operation_get` | `GET /stock/fulfillment/operations/{operation_id}` | `allowlisted_redacted` | `token_scoped` | operation_id | [oficial](https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas/envios-fulfillment) |
| `ml.stock.user_product` | `GET /user-products/{user_product_id}/stock` | `allowlisted_redacted` | `owned_user_product` | user_product_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/estoque-distribuido) |
| `ml.stock.locations` | `GET /users/{user_id}/stores/search` | `allowlisted_redacted` | `seller` | user_id, tags, limit, offset; fixos: tags=stock_location | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/estoque-distribuido) |

### Mensagens pós-venda

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.messages.conversation` | `GET /messages/packs/{pack_id}/sellers/{user_id}` | `allowlisted_redacted` | `owned_pack` | pack_id, user_id, limit, offset; fixos: tag=post_sale, mark_as_read=false | [oficial](https://developers.mercadolivre.com.br/mensagens-post-venda) |
| `ml.messages.get` | `GET /messages/{message_id}` | `document_only` | `token_scoped` | message_id; fixos: tag=post_sale | [oficial](https://developers.mercadolivre.com.br/mensagens-post-venda) |
| `ml.messages.unread` | `GET /messages/unread` | `allowlisted_redacted` | `seller` | role, limit; fixos: tag=post_sale, role=seller | [oficial](https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes) |
| `ml.messages.unread_resource` | `GET /messages/unread/{resource}` | `document_only` | `seller` | resource; fixos: tag=post_sale | [oficial](https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes) |

### Reclamações

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.claims.search` | `GET /post-purchase/v1/claims/search` | `allowlisted_redacted` | `seller` | status, stage, type, resource, resource_id, date_created.from, date_created.to, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.get` | `GET /post-purchase/v1/claims/{claim_id}` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.detail` | `GET /post-purchase/v1/claims/{claim_id}/detail` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.actions_history` | `GET /post-purchase/v1/claims/{claim_id}/actions-history` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.status_history` | `GET /post-purchase/v1/claims/{claim_id}/status-history` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.affects_reputation` | `GET /post-purchase/v1/claims/{claim_id}/affects-reputation` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.messages` | `GET /post-purchase/v1/claims/{claim_id}/messages` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.evidences` | `GET /post-purchase/v1/claims/{claim_id}/evidences` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.partial_refund_offers` | `GET /post-purchase/v1/claims/{claim_id}/partial-refund/available-offers` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.attachment_meta` | `GET /post-purchase/v1/claims/{claim_id}/attachments/{attachment_id}` | `document_only` | `owned_claim` | claim_id, attachment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.attachment_download` | `GET /post-purchase/v1/claims/{claim_id}/attachments/{attachment_id}/download` | `document_only` | `owned_claim` | claim_id, attachment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.evidence_attachment_meta` | `GET /post-purchase/v1/claims/{claim_id}/attachments-evidences/{attachment_id}` | `document_only` | `owned_claim` | claim_id, attachment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |
| `ml.claims.evidence_attachment_download` | `GET /post-purchase/v1/claims/{claim_id}/attachments-evidences/{attachment_id}/download` | `document_only` | `owned_claim` | claim_id, attachment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes) |

### Devoluções

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.returns.get_by_claim` | `GET /post-purchase/v2/claims/{claim_id}/returns` | `allowlisted_redacted` | `owned_claim` | claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-devolucoes) |
| `ml.returns.review` | `GET /post-purchase/v1/returns/{return_id}/reviews` | `document_only` | `token_scoped` | return_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-devolucoes) |
| `ml.returns.reasons` | `GET /post-purchase/v1/returns/reasons` | `allowlisted_redacted` | `token_scoped` | flow, claim_id | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-devolucoes) |
| `ml.returns.cost` | `GET /post-purchase/v1/claims/{claim_id}/charges/return-cost` | `allowlisted_redacted` | `owned_claim` | claim_id, calculate_amount_usd | [oficial](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-devolucoes) |

### Promoções

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.promotions.user` | `GET /seller-promotions/users/{user_id}` | `delegated` | `seller` | user_id; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.candidate` | `GET /seller-promotions/candidates/{candidate_id}` | `allowlisted_redacted` | `token_scoped` | candidate_id; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.offer` | `GET /seller-promotions/offers/{offer_id}` | `allowlisted_redacted` | `token_scoped` | offer_id; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.get` | `GET /seller-promotions/promotions/{promotion_id}` | `allowlisted_redacted` | `token_scoped` | promotion_id, promotion_type; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.items` | `GET /seller-promotions/promotions/{promotion_id}/items` | `allowlisted_redacted` | `token_scoped` | promotion_id, promotion_type, item_id, status, status_item, limit, search_after; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.item` | `GET /seller-promotions/items/{item_id}` | `allowlisted_redacted` | `owned_item` | item_id; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.exclusion_list` | `GET /seller-promotions/exclusion-list/seller` | `allowlisted_redacted` | `seller` | —; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |
| `ml.promotions.exclusion_item` | `GET /seller-promotions/exclusion-list/seller/{item_id}` | `allowlisted_redacted` | `owned_item` | item_id; fixos: app_version=v2 | [oficial](https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas) |

### Faturamento

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.billing.periods` | `GET /billing/integration/monthly/periods` | `allowlisted_redacted` | `token_scoped` | document_type, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.documents` | `GET /billing/integration/periods/key/{period_key}/documents` | `allowlisted_redacted` | `token_scoped` | period_key, group, document_type, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.summary_details` | `GET /billing/integration/periods/key/{period_key}/summary/details` | `allowlisted_redacted` | `token_scoped` | period_key | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.ml_details` | `GET /billing/integration/periods/key/{period_key}/group/ML/details` | `allowlisted_redacted` | `token_scoped` | period_key, document_type, limit, from_id | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.mp_details` | `GET /billing/integration/periods/key/{period_key}/group/MP/details` | `document_only` | `token_scoped` | period_key | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.order_details` | `GET /billing/integration/group/ML/order/details` | `allowlisted_redacted` | `token_scoped` | order_ids | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.payment_details` | `GET /billing/integration/periods/key/{period_key}/group/ML/payment/details` | `allowlisted_redacted` | `token_scoped` | period_key, limit, from_id | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.payment_charges` | `GET /billing/integration/payment/{payment_id}/charges` | `allowlisted_redacted` | `token_scoped` | payment_id, sort, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.perceptions_summary` | `GET /billing/integration/periods/key/{period_key}/perceptions/summary` | `document_only` | `token_scoped` | period_key | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.legal_document` | `GET /billing/integration/legal_document/{file_id}` | `document_only` | `token_scoped` | file_id | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |
| `ml.billing.report_download` | `GET /billing/integration/reports/{file_id}` | `document_only` | `token_scoped` | file_id | [oficial](https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento) |

### Notas fiscais

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.invoices.get` | `GET /users/{user_id}/invoices/{invoice_id}` | `document_only` | `seller` | user_id, invoice_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/obtendo-nota-fiscal) |
| `ml.invoices.by_order` | `GET /users/{user_id}/invoices/orders/{order_id}` | `document_only` | `owned_order` | user_id, order_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/obtendo-nota-fiscal) |
| `ml.invoices.by_shipment` | `GET /users/{user_id}/invoices/shipments/{shipment_id}` | `document_only` | `owned_shipment` | user_id, shipment_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/obtendo-nota-fiscal) |
| `ml.invoices.xml_authorized` | `GET /users/{user_id}/invoices/documents/xml/{invoice_id}/authorized` | `document_only` | `seller` | user_id, invoice_id | [oficial](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/obtendo-nota-fiscal) |

### Product Ads

| Resource ID | Método e rota | Política | Propriedade | Parâmetros permitidos | Fonte |
|---|---|---|---|---|---|
| `ml.ads.advertisers` | `GET /advertising/advertisers` | `document_only` | `token_scoped` | product_id | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |
| `ml.ads.item` | `GET /advertising/{site_id}/product_ads/ads/{item_id}` | `document_only` | `owned_item` | site_id, item_id | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |
| `ml.ads.ads_search` | `GET /advertising/{site_id}/advertisers/{advertiser_id}/product_ads/ads/search` | `document_only` | `token_scoped` | site_id, advertiser_id, date_from, date_to, metrics, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |
| `ml.ads.campaigns_search` | `GET /advertising/{site_id}/advertisers/{advertiser_id}/product_ads/campaigns/search` | `document_only` | `token_scoped` | site_id, advertiser_id, date_from, date_to, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |
| `ml.ads.campaign_metrics` | `GET /advertising/{site_id}/product_ads/campaigns/{campaign_id}` | `document_only` | `token_scoped` | site_id, campaign_id, date_from, date_to, metrics, aggregation | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |
| `ml.ads.ad_groups_search` | `GET /advertising/{site_id}/advertisers/{advertiser_id}/product_ads/ad_groups/search` | `document_only` | `token_scoped` | site_id, advertiser_id, offset, limit | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |
| `ml.ads.ad_group_metrics` | `GET /advertising/{site_id}/product_ads/campaigns/{campaign_id}/ad_groups/metrics` | `document_only` | `token_scoped` | site_id, campaign_id, date_from, date_to, metrics | [oficial](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura) |

## Quando usar as funções especializadas

| Necessidade | Função preferencial |
|---|---|
| Anúncio, descrição e imagens | `mercado_livre_listing` |
| Visitas de um MLB exato | `mercado_livre_visits` |
| Promoções da conta | `mercado_livre_promotions` |
| Pedidos e packs | `mercado_livre_orders` |
| Fila de perguntas | `questions_post_sale_query` |
| Conversa pós-venda de pack exato | `mercado_livre_post_sale_detail` |
| Estoque Full agregado existente | `mercado_livre_full_stock` |
| Qualquer outro GET catalogado | `mercado_livre_resource_query` |

## Paginação e cobertura

- Use `limit` e `offset` somente quando estiverem listados na rota.
- O executor limita `limit` a 100, `offset` a 1.000, IDs a 20 por chamada e `order_ids` a 60.
- Datas precisam usar `YYYY-MM-DD`, não podem estar no futuro e ficam limitadas à janela geral de 366 dias; recursos mais restritos mantêm seus próprios limites.
- Rotas com `scroll_id`, `search_after` ou `from_id` devem continuar a partir do cursor retornado, sem misturar contas.
- Um resultado vazio só é conclusivo para a loja e o filtro efetivamente consultados.
- HTTP 206, timeout, rate limit ou falha de uma página tornam a cobertura parcial; não transforme ausência parcial em zero confirmado.

## Segurança e atualização

A autenticação deve acompanhar cada chamada e corresponder ao usuário consultado. Segredos e dados pessoais não podem aparecer em logs. A camada de transporte usa validação TLS; proxy corporativo deve configurar uma CA confiável em `ML_CA_BUNDLE`.

Ao atualizar este documento:

1. Revisar o índice e os changelogs oficiais.
2. Alterar primeiro o catálogo em código.
3. Atualizar versão e data.
4. Executar testes de contrato, propriedade, DLP e transporte.
5. Gerar o manifest do Context Bundle.
6. Publicar a geração pelo Context Hub; uma geração inválida nunca substitui a ativa.

## Portas de entrada oficiais

- [Índice da documentação da API](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br-1)
- [Desenvolvimento seguro e OAuth](https://developers.mercadolivre.com.br/pt_br/autenticacao-e-autorizacao/desenvolvimento-seguro)
- [Práticas de cybersecurity](https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/praticas-de-cybersecurity)
- [Itens e buscas](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas)
- [Gerenciamento de vendas](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas)
- [Gerenciamento de envios](https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios)
- [Perguntas e respostas](https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas)
- [Reclamações](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes)
- [Devoluções](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-devolucoes)
- [Product Ads — leitura](https://developers.mercadolivre.com.br/pt_br/product-ads-leitura)

## Histórico

- **2026-07-20.1** — primeiro catálogo unificado: 165 recursos, execução fechada de GETs allowlisted, funções especializadas delegadas, POSTs consultivos e Ads em modo documental, endpoint removido registrado como tombstone.
