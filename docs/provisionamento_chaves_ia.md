# Provisionamento de chaves de IA

## Fluxo

1. O Admin cadastra ou troca as chaves na tela de Configuracoes.
2. O backend salva as chaves no cofre local do Windows quando disponivel.
3. Se `JK_SECRETS_PROVISIONING_URL` e `JK_SECRETS_PROVISIONING_ADMIN_TOKEN` estiverem configurados, o backend publica o pacote completo no servidor de provisionamento.
4. O servidor de provisionamento grava todas as chaves juntas em um unico secret JSON no Google Secret Manager.
5. Quando qualquer usuario ativo faz login, o `auth.js` chama `/api/ia/secrets/provisionar`.
6. O backend valida a sessao, baixa o pacote do provisionador e salva as chaves no Cofre do Windows.
7. As proximas chamadas de IA leem as chaves do Cofre do Windows, sem mostrar valores na tela.

## Secret JSON

Use um unico secret, por exemplo `jk-app-ia-secrets`, com este formato:

```json
{
  "OPENAI_API_KEY": "...",
  "DEEPSEEK_API_KEY": "...",
  "GEMINI_API_KEY": "...",
  "GEMINI_AGENT_API_KEY": "...",
  "GROQ_API_KEY": "..."
}
```

## Variaveis do backend local

```text
JK_SECRETS_PROVISIONING_URL=https://SEU-WORKER.workers.dev
JK_SECRETS_PROVISIONING_DOWNLOAD_TOKEN=token-de-download
JK_SECRETS_PROVISIONING_ADMIN_TOKEN=token-admin
```

O token de download permite que o backend local baixe as chaves para o Cofre do Windows. O token admin permite publicar novas versoes pelo endpoint `/admin/secrets`.

## Secrets do Cloudflare Worker

Configure estes valores como secrets do Worker:

```text
ADMIN_TOKEN
DOWNLOAD_TOKEN
GCP_CLIENT_EMAIL
GCP_PRIVATE_KEY
GCP_PROJECT_ID
GCP_SECRET_ID
```

`GCP_SECRET_ID` deve apontar para o secret unico, por exemplo `jk-app-ia-secrets`.

## Permissao minima no Google Cloud

A service account usada pelo Worker deve ter permissao apenas para acessar e alterar o secret unico de IA. Evite permissoes amplas como Owner ou Editor no projeto.

## Arquivos locais legados

Quando o Cofre do Windows esta disponivel, o backend remove os arquivos locais legados das chaves de IA depois de salvar no cofre.
