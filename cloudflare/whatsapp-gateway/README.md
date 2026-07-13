# JK WhatsApp Gateway (custo zero)

Gateway isolado para WhatsApp Cloud API, Cloudflare Workers Free, D1 e Workers KV Free.

## Preparacao

1. Copie `wrangler.example.toml` para `wrangler.toml`.
2. Crie o D1 `jk-whatsapp-gateway` e aplique todas as migrations em ordem com `wrangler d1 migrations apply jk-whatsapp-gateway --remote`.
3. Crie o namespace Workers KV privado `jk-whatsapp-media`.
4. Preencha o `database_id` e a versao atual da Graph API.
5. Cadastre os secrets listados no arquivo de exemplo.
6. Execute `npm install`, `npm test`, `npm run check` e `npm run deploy`.

Na raiz do JK, `scripts/setup-whatsapp-zero-cost.ps1` automatiza D1, Workers KV com expiracao de 24 horas, secrets, deploy, teste de saude e submissao dos templates. Execute com:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-whatsapp-zero-cost.ps1 -BusinessPhone "+55..."
```

A inscricao do callback no campo `messages` continua sendo confirmada no painel Meta. A integracao local permanece desativada ate o Whisper Small ser baixado e todos os indicadores ficarem prontos.

## Regra inegociavel

O gateway nunca envia fora da janela gratuita de 23h30 nem depois de `ZERO_COST_POLICY_VALID_UNTIL`. Templates de marketing/autenticacao nao sao aceitos.
