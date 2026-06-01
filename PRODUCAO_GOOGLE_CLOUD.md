# Producao no Google Cloud

## Recursos criados

- Projeto: `jk-sistema-prod-20260525`
- Regiao: `southamerica-east1`
- Cloud Run: `jk-sistema-api`
- URL: `https://jk-sistema-api-1077918177671.southamerica-east1.run.app`
- Cloud SQL PostgreSQL: `jk-sistema-postgres`
- Banco: `jk_sistema`
- Bucket persistente da pasta `info`: `gs://jk-sistema-prod-20260525-info`
- Artifact Registry Docker: `jk-sistema`

## Secrets

- `jk-db-password`
- `jk-database-url`
- `jk-openai-api-key`
- `jk-gemini-api-key`
- `jk-deepseek-api-key`

## Deploy

Use:

```bat
deploy_cloud_run.bat
```

Ou rode o comando base:

```bat
gcloud.cmd run deploy jk-sistema-api ^
  --source . ^
  --project jk-sistema-prod-20260525 ^
  --region southamerica-east1 ^
  --allow-unauthenticated
```

O script completo tambem conecta Cloud SQL, monta o bucket da pasta `info` em `/mnt/jk-info` e injeta os secrets.
Ele faz o build no repositório `jk-sistema` e publica a imagem com `--image`.

## Pos-deploy

1. Configure o callback do Bling e do Mercado Livre como:

```text
https://jk-sistema-api-1077918177671.southamerica-east1.run.app/auth/callback
```

2. Se a URL mudar, atualize o Cloud Run com:

```bat
gcloud.cmd run services update jk-sistema-api ^
  --project jk-sistema-prod-20260525 ^
  --region southamerica-east1 ^
  --update-env-vars JK_REDIRECT_URI=https://SUA_URL_DO_CLOUD_RUN/auth/callback,JK_BLING_REDIRECT_URI=https://SUA_URL_DO_CLOUD_RUN/auth/callback,JK_AUTO_NGROK_REDIRECT=false
```

## Observacoes

- O app ainda usa arquivos SQLite/JSON na pasta `info`. Por isso, a pasta foi enviada para um bucket e montada no Cloud Run.
- O Cloud SQL ja esta pronto e conectado como `DATABASE_URL`, mas migrar todos os SQLite/JSON para PostgreSQL ainda e uma etapa separada.
- A imagem de producao nao instala Chromium/Playwright. Se uma automacao precisar de navegador headless no servidor, crie um worker separado para isso.
- O `gcloud` desta maquina esta com erro de certificado local. Os scripts usam `CLOUDSDK_AUTH_DISABLE_SSL_VALIDATION=true` apenas para conseguir executar daqui.
