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
validada. `.obsidian` é criado apenas quando ausente, sem plugins comunitários
e sem um `workspace.json` predefinido. `80_Curadoria` e `90_Arquivo` nunca são
apagados ou sobrescritos pelo gerador.
