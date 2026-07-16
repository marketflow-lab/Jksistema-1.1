# VPS SIP para ligações do Black Jhon

Esta pasta contém a borda SIP/TLS. A chave OpenAI **não fica no VPS**: ela permanece no cofre local do JK Sistema e no Secret do Cloudflare Worker.

## Pré-requisitos

- VPS Linux com IPv4 público e DNS próprio;
- certificado TLS válido para `PUBLIC_SIP_HOST`;
- projeto OpenAI habilitado para Realtime SIP;
- origem/rede SIP fornecida pela configuração de Calling da Meta;
- portfólio empresarial verificado e limite de mensagens da Meta igual ou superior a `TIER_2K`;
- portas `5061/tcp` e a faixa RTP escolhida liberadas somente conforme a necessidade do provedor.

## Implantação

1. Copie `.env.example` para `.env` e preencha os valores reais.
2. Coloque `fullchain.pem` e `privkey.pem` em `secrets/tls/`.
3. Defina `META_SIP_ALLOWED_CIDRS` com as redes oficiais exibidas na configuração da conta. O contêiner recusa inicialização com redes universais.
4. Execute `docker compose config` e depois `docker compose up -d --build`.
5. Cadastre o destino SIP/TLS do número empresarial como `sip:<numero-ou-usuario>@PUBLIC_SIP_HOST:5061`, conforme o contrato oferecido pela conta Meta.
6. No Cloudflare, configure `VOICE_SIP_HOST=PUBLIC_SIP_HOST` e o webhook OpenAI em `/webhooks/openai/realtime`.

## Preflight obrigatório

1. Consulte `GET /bridge/meta/calling/status` pelo backend autenticado.
2. Confirme `eligibility.owner_business.data.verification_status=verified` e limite igual ou superior a `TIER_2K`.
3. Execute `POST /bridge/meta/calling/prepare`. Essa operação cadastra o SIP mantendo Calling, ícone e callback desabilitados.
4. Somente abra SIP/RTP no firewall depois que a Meta aceitar a preparação, as redes oficiais da Meta estiverem na allowlist e o webhook OpenAI estiver validado.

Se a Meta retornar o código `138015` com a mensagem `Calling APIs cannot be enabled for this phone number`, mantenha o firewall fechado. O erro indica inelegibilidade do número/portfólio e não deve ser contornado por uma configuração SIP genérica.

## Segurança e operação

- O firewall do VPS deve repetir a allowlist configurada no Kamailio.
- O Kamailio preserva os cabeçalhos de identidade da chamada; o Worker só aceita a sessão após validar a assinatura OpenAI, o telefone vinculado e a permissão individual.
- O destino OpenAI é sempre `sip:${OPENAI_PROJECT_ID}@sip.api.openai.com;transport=tls`.
- `rtpengine` trata somente mídia em trânsito. O compose não monta volume de gravação e não persiste áudio.
- Não habilite ligações na interface antes que o preflight marque todos os itens como prontos.

Os endereços/redes SIP e o formato exato do destino de entrada dependem do recurso Calling liberado na conta Meta; por isso não há valores fictícios embutidos nesta configuração.
