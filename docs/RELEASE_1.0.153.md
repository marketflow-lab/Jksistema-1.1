# JK Sistema 1.0.153

- O contexto enviado ao agente de respostas do Mercado Livre deixou de transportar identificadores de tenant, loja, seller, site e pedido.
- Metadados de execucao, migracao, autorizacao de pesquisa e controle do Context Hub permanecem somente no backend.
- SKU, anuncio e variacao passaram a ser enviados uma unica vez em uma identidade compacta.
- Documentos canonicos, catalogo e orientacoes gerais e por SKU do Obsidian continuam integrais.
- A mesma reducao e aplicada quando o Context Hub volta como resultado de uma pesquisa do agente.

[Validacao desta versao](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.153/docs/RELEASE_1.0.153_VALIDACAO.md).

Atualizacao pequena no perfil `app-update`, preservando o runtime existente.
