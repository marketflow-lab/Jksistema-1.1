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
80_Curadoria/
  ADRs/
  Regras/
  Notas/
90_Arquivo/
```

`70_Gerado` é substituído apenas por publicação atômica de uma geração
validada. `.obsidian` é criado sem plugins comunitários e sem um
`workspace.json` predefinido. Quando `graph.json` ainda não existe, o bootstrap
instala seis grupos visuais nativos: mapas e domínios em azul; categorias em
âmbar; catálogo, famílias e cobertura SKU em rosa; montadoras em verde; raízes
e índices de veículos em violeta; e modelos ou aplicações em ciano. Notas gerais
permanecem na cor neutra do tema. Um `graph.json` já existente é sempre
preservado como preferência do usuário. `80_Curadoria` e `90_Arquivo` nunca são
apagados ou sobrescritos pelo gerador.
