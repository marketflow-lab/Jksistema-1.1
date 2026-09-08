# Conhecimento técnico do JK Sistema

Esta pasta contém somente conhecimento técnico versionado e seguro para o
Context Hub. Ela não é o vault editável do usuário e não pode receber dados de
clientes, credenciais, bancos operacionais ou dossiês SKU.

No runtime, o conteúdo listado no `context-bundle-manifest.json` da raiz do
pacote é importado para uma geração em staging. Arquivos ausentes do manifesto
são ignorados. O vault persistente fica em `info/<client_id>/ContextVault`,
enquanto o banco e o journal ficam em `info/<client_id>/context_hub`.

## Guias versionados

- [Orientação de busca na API do Mercado Livre](mercado-livre-api-consultas.md) — catálogo oficial de endpoints de consulta, regras de segurança e funções disponíveis no assistente interno.
- [Árvore de categorias de produto SKU](sku-product-category-taxonomy-v1.md) — taxonomia aprovada e versionada usada para gerar os índices de produto no Obsidian.
- [Árvore SKU por veículo e ano](sku-vehicle-year-tree-v1.md) — contrato versionado da visão derivada montadora → modelo → ano → SKUs.
- [Conhecimento integral por loja e SKU V18](store-sku-knowledge-v18.md) — isolamento do módulo de perguntas públicas, curadoria schema 3, migração e limites sem truncamento.

## Classes de verdade

- `source`: extraído diretamente do código ou contrato atual.
- `canonical`: fonte de negócio deliberadamente mantida, como o catálogo SKU.
- `generated`: mapa derivado de fontes atuais.
- `generated_secondary`: registro auxiliar que não substitui código ou API.
- `legacy_unverified`: documentação antiga, não promovida sem curadoria.

O diretório `70_Gerado` é administrado pelo publicador. Notas humanas devem
ficar em `80_Curadoria` e só entram no contexto quando declaram
`status: published` e `ai_usage: allowed`.
