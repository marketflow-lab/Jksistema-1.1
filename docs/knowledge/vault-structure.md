# Estrutura do ContextVault

O publicador cria a estrutura abaixo sem substituir arquivos humanos:

```text
00_Inicio/
70_Gerado/
  Mapas/
  Dominios/
  Fluxos/
  Contratos/
  Operacao/
  Produtos/
  Lojas/
    <store_id--loja>/
      SKUs/
        <sku>/
          Contexto.md
80_Curadoria/
  ADRs/
  Regras/
  Notas/
  Lojas/
    <store_id--loja>/
      Orientacoes-Gerais.md
      SKUs/
        <sku>/
          Orientacoes.md
90_Arquivo/
  Quarentena/
    SKUs-sem-identidade/
```

`70_Gerado` é substituído apenas por publicação atômica de uma geração
validada. `.obsidian` é criado sem plugins comunitários e sem um
`workspace.json` predefinido. Quando `graph.json` ainda não existe, o bootstrap
instala seis grupos visuais nativos: mapas e domínios em azul; categorias em
âmbar; catálogo, famílias e cobertura SKU em rosa; montadoras em verde; raízes
e índices de veículos em violeta; e modelos ou aplicações em ciano. Notas gerais
permanecem na cor neutra do tema. Os padrões são publicados de forma atômica e
somente quando `graph.json` ainda não existe; se o Obsidian criar o arquivo ao
mesmo tempo, a versão do Obsidian vence. Um `graph.json` existente é sempre
preservado byte a byte, mesmo que não contenha todos os grupos gerenciados ou
tenha JSON inválido. Grupos ausentes devem ser configurados pela interface do
Obsidian. O `workspace.json` nunca é alterado.
`80_Curadoria` e `90_Arquivo` nunca são apagados ou sobrescritos pelo gerador.

Para perguntas públicas do Mercado Livre, `70_Gerado/Lojas` representa uma
geração ativa independente por loja. O leitor exige tenant, `store_id`, seller,
site, item, variação (quando houver) e SKU exatos; ele não consulta a geração
global como fallback. Arquivos em quarentena têm `ai_usage: denied`.
