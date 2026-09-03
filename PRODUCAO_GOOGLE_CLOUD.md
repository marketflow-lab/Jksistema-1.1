# Producao no Google Cloud

## Estado atual

- Projeto: `jk-sistema-prod-20260525`
- Regiao usada antes: `southamerica-east1`
- Cloud Run removido/desativado
- Cloud SQL removido/desativado
- Cloud Storage removido/desativado
- Cloud Build removido/desativado
- Registro de imagens Docker removido/desativado

Esse estado descreve o antigo backend completo. O código agora contém um gateway isolado de autenticação
em `cloud/auth_gateway`, mas ele ainda não está implantado e não reativa o backend completo em nuvem.

## Secrets mantidos

- `jk-openai-api-key`
- `jk-gemini-api-key`
- `jk-deepseek-api-key`

## Deploy Google Cloud

O deploy do antigo backend completo no Google Cloud continua removido deste projeto local.

O app continua rodando localmente/desktop e sendo versionado pelo GitHub. A única exceção preparada é o
gateway mínimo `jk-auth-gateway`, necessário para que uma instalação limpa valide usuários sem receber
credenciais administrativas. Sua ativação exige autorização específica para faturamento e deploy e deve
seguir `cloud/auth_gateway/README.md`.

## Callbacks

Se algum dia o deploy em nuvem for reativado, crie uma nova URL publica e configure o callback do Bling e do Mercado Livre nela:

```text
https://SUA_URL_PUBLICA/auth/callback
```

## Observacoes

- Os servicos de deploy/execucao em nuvem foram removidos para evitar cobranca desnecessaria.
- Nenhum deploy, API ou faturamento é ativado apenas pela presença do código do gateway.
- As integracoes Bling e Mercado Livre continuam dependendo apenas do callback configurado e das credenciais corretas.
