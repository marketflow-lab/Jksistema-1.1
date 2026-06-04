# Bug Hunter Mercado Livre

Este Bug Hunter usa Playwright para abrir o sistema, fazer login com conta de teste, navegar pelo fluxo de anuncios Mercado Livre, capturar erros de console/API e gerar relatorio. Ele nao corrige bugs automaticamente.

## Comandos

```bash
npm run bug-hunter:install
npm run bug-hunter
npm run bug-hunter:headed
npm run bug-hunter:report
```

O relatorio e gerado em:

```text
bug-hunter-report/bug-hunter-report.md
bug-hunter-report/bug-hunter-report.json
```

## Secrets

Configure no GitHub:

```text
BUG_HUNTER_BASE_URL
BUG_HUNTER_USERNAME ou BUG_HUNTER_EMAIL
BUG_HUNTER_PASSWORD
BUG_HUNTER_TEST_LISTING_ID
BUG_HUNTER_TEST_SKU
BUG_HUNTER_STORE
MELI_SANDBOX
```

`BUG_HUNTER_DRY_RUN` deve ficar `true` por padrao. Para qualquer mutacao real, alem de autorizacao humana, use `BUG_HUNTER_ALLOW_MUTATIONS=true` e limite a execucao a sandbox ou ao anuncio de teste.

## Travas

- Em dry-run, `POST`, `PUT`, `PATCH` e `DELETE` para Mercado Livre sao interceptados e respondidos localmente.
- Sem dry-run, a chamada so passa se `BUG_HUNTER_ALLOW_MUTATIONS=true` e houver sandbox ou o corpo da request contiver `BUG_HUNTER_TEST_LISTING_ID`.
- Tokens, senhas e segredos sao mascarados no relatorio.
- O fluxo de titulo registra bug se a tela nao expuser controles de edicao de titulo.

## Politica de correcao

Primeiro rode o Bug Hunter e leia o relatorio. Depois autorize explicitamente quais bugs quer corrigir.
