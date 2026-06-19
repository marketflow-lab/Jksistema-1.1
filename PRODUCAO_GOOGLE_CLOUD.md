# Producao no Google Cloud

## Estado atual

- Projeto: `jk-sistema-prod-20260525`
- Regiao usada antes: `southamerica-east1`
- Cloud Run removido/desativado
- Cloud SQL removido/desativado
- Cloud Storage removido/desativado
- Cloud Build removido/desativado
- Registro de imagens Docker removido/desativado

## Secrets mantidos

- `jk-openai-api-key`
- `jk-gemini-api-key`
- `jk-deepseek-api-key`

## Deploy Google Cloud

O deploy no Google Cloud foi removido deste projeto local.

O app atual deve rodar localmente/desktop e ser versionado pelo GitHub, sem publicar imagem Docker em servico de registro do Google.

## Callbacks

Se algum dia o deploy em nuvem for reativado, crie uma nova URL publica e configure o callback do Bling e do Mercado Livre nela:

```text
https://SUA_URL_PUBLICA/auth/callback
```

## Observacoes

- Os servicos de deploy/execucao em nuvem foram removidos para evitar cobranca desnecessaria.
- As integracoes Bling e Mercado Livre continuam dependendo apenas do callback configurado e das credenciais corretas.
