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

## Ligações Realtime SIP

A migração `0006_voice_calls.sql` adiciona somente estado, auditoria e consumo técnico das chamadas. Ela não grava áudio nem transcrição. A transcrição concluída é persistida apenas pelo backend local no Histórico do WhatsApp.

Para ativar voz em uma instalação existente:

1. implante o VPS descrito em `infra/whatsapp-voice` e configure `VOICE_SIP_HOST`;
2. crie o webhook OpenAI apontando para `https://<worker>/webhooks/openai/realtime` e assine eventos Realtime;
3. execute `scripts/deploy-whatsapp-voice.ps1 -VoiceSipHost voice.seudominio.com`;
4. abra Configurações, valide todos os componentes e autorize ligações somente nos telefones desejados;
5. habilite a voz somente depois que o preflight estiver integralmente pronto.

O script reutiliza `_obter_openai_api_key()` e envia a chave diretamente ao Wrangler. O valor não é gravado no TOML, no D1 ou em arquivos do projeto. `OPENAI_WEBHOOK_SECRET` e `OPENAI_PROJECT_ID` são solicitados como valores protegidos.

## Regra inegociavel

O gateway nunca envia fora da janela gratuita de 23h30 nem depois de `ZERO_COST_POLICY_VALID_UNTIL`. Templates de marketing/autenticacao nao sao aceitos.

Essa regra de mensagens não elimina a cobrança própria de WhatsApp Calling, SIP/VPS ou OpenAI Realtime. A tela de voz mostra duração e tokens, mas não inventa um custo monetário local.
