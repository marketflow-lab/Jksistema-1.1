# Cofre de Informações: gateway por empresa

Worker e banco D1 próprios. Não depende do gateway WhatsApp, Firebase, Firestore,
KV, filas ou cron. Em produção, o endereço previsto é
`https://jk-cofre-gateway.jk-sistema.workers.dev`.

O processo de desenvolvimento deste pacote não cria recursos e não publica o
Worker. A configuração versionada é somente um exemplo; o identificador real do
D1 fica em `wrangler.toml`, ignorado pelo Git. O banco precisa ser exclusivo deste
serviço. A migração `migrations/0001_company_state.sql` cria uma linha por empresa.

## Protocolo

Todas as respostas usam `Cache-Control: no-store`. A API não publica cabeçalhos
CORS, não oferece autenticação por cookie e não registra requisições ou dados.

- `GET /v1/state`: `{revision,state}`; uma empresa nova recebe revisão zero e
  `{format:"jk-cofre-shared-v1",guilds:[]}`.
- `GET /v1/state?after=N`: retorna `304` e ETag se a revisão for exatamente `N`;
  se for diferente, retorna o estado atual. O valor maior que a revisão atual
  também recebe `200`, para permitir recuperação de um cache desatualizado.
- `PUT /v1/state`: recebe `{expected_revision:N,state:OBJ}`; retorna
  `{revision:N+1}`. A primeira gravação exige revisão zero. Cada CAS usa uma única
  instrução SQL atômica. Em concorrência, somente uma gravação vence; as outras
  recebem `409 {error:"revision_conflict",revision:ATUAL}` e devem reler/conciliar.

Não há parâmetro de empresa. A chave D1 vem exclusivamente de `client_id` no JWT.
Campos ou parâmetros extras são rejeitados. A coluna `state` contém o último
snapshot; o serviço não faz mesclagem silenciosa nem mantém histórico de versões.
Exclusões recuperáveis são os registros de mensagem com `deleted:true` no estado.
Para preservar edições confirmadas durante futuras importações, canais e
mensagens também aceitam `edited_fields`: uma lista sem repetição das chaves
cujos valores efetivos devem recriar os overlays locais. Mensagens aceitam no
máximo quatro itens: `content`, `author`, `links`, `embeds`. Canais aceitam no
máximo três: `name`, `category`, `topic`. A lista vazia é o padrão; omitir o campo
tem o mesmo significado. O Worker preserva a lista e os valores do snapshot sem
aplicar mesclagem. A flag de interface `edited` continua proibida.

O estado aceita somente o schema fechado:

```json
{"format":"jk-cofre-shared-v1","guilds":[{"guild":{"id":"server1","name":"Servidor"},"exported_at":"2026-10-22T00:00:00Z","channels":[{"id":"channel1","name":"guias","category":"Guias","url":"https://discord.com/channels/server1/channel1","topic":null,"history_complete":true,"kind":"text","messages":[{"id":"message1","author":"Autor","timestamp":"2026-10-22T00:00:00Z","content":"Texto","links":[],"attachments":[],"embeds":[],"source_url":"https://discord.com/channels/server1/channel1/message1","deleted":false}]}]}]}
```

Campos opcionais originais do canal são `reached_start`, `reached_end` e
`last_capture_at`. O Worker também aceita os valores padrão omitidos dos campos
originais de texto/lista. `deleted` é obrigatório. Anexos compartilhados contêm
somente `url` HTTP(S) não vazia, `filename` e `content_type` opcional. Caminhos
locais, URLs internas de arquivos, flags de interface e contagens não pertencem
ao protocolo. Binários ficam fora do D1. Texto e conteúdo HTML são dados opacos.

O snapshot serializado em UTF-8 tem limite de **1 MiB**. A requisição inteira tem
limite de **1 MiB + 64 KiB**, inclusive quando não declara `Content-Length`.
Identificadores, URLs, tipos, duplicação de IDs e limites de coleções são validados
antes de qualquer gravação. O D1 repete o limite de tamanho por uma restrição SQL.

## Autenticação

O backend emite JWT RS256 com cabeçalho `alg:"RS256",typ:"JWT"` e `kid` opcional.
Os únicos claims aceitos são `iss,aud,sub,client_id,machine_hash,scope,iat,exp`.
`aud` precisa ser `jk-cofre`, `iss` precisa coincidir com `COFRE_ISSUER_EMAIL`,
`machine_hash` tem 64 caracteres hexadecimais e o prazo máximo é de oito horas.
JWT expirado é rejeitado. Aceita-se desvio de até 60 segundos apenas no `iat`.

Leitura exige `cofre:read`. Gravação exige `cofre:read` e `cofre:write`; a decisão
de conceder escrita a `full`/`admin_usuarios` é feita pelo emissor autenticado.
Não se confia em papéis ou tenant enviados no corpo do pedido.

As chaves públicas são buscadas somente no endpoint Google fixo
`https://www.googleapis.com/service_accounts/v1/jwk/{issuer-configurado}`.
Há cache em memória, deduplicação de consultas simultâneas, prazo de 60 a 3600
segundos conforme o cache público e timeout de cinco segundos. Não há fallback
para chaves fornecidas no token, certificados remotos arbitrários ou banco de
usuários. Sem `kid`, tentam-se no máximo dez chaves RSA do conjunto confiável.
A identidade de assinatura pública está no exemplo; nenhuma chave privada é
implantada no Worker.

## Validação e operação

```powershell
npm.cmd ci --ignore-scripts
npm.cmd run check
npm.cmd test
```

As dependências de desenvolvimento estão fixadas no lockfile; o Worker não tem
dependências de runtime. A auditoria da instalação fixa precisa continuar sem
vulnerabilidades ao atualizar ferramentas.

## Provisionamento e publicação

Os comandos abaixo são para execução posterior pelo responsável pela conta,
no diretório deste pacote. O login precisa selecionar a conta destinada ao
endereço previsto. Não reutilize o D1 de outro serviço.

```powershell
$env:WRANGLER_SEND_METRICS = 'false'
$env:WRANGLER_LOG_PATH = Join-Path (Get-Location) '.wrangler/logs'
npx.cmd --no-install wrangler login
Copy-Item -LiteralPath wrangler.example.toml -Destination wrangler.toml
npx.cmd --no-install wrangler d1 create jk-cofre-company --config wrangler.toml --update-config=false
```

Preencha `database_id` no arquivo local `wrangler.toml` com o UUID retornado e
confira a conta escolhida. O exemplo já declara `binding = "DB"` e o diretório
de migrações. O arquivo local é ignorado pelo Git; não altere a configuração do
gateway WhatsApp. Em seguida:

```powershell
npx.cmd --no-install wrangler deploy --dry-run --config wrangler.toml --outdir .wrangler/dry-run
npx.cmd --no-install wrangler d1 migrations apply jk-cofre-company --remote --config wrangler.toml
npx.cmd --no-install wrangler deploy --config wrangler.toml
```

O comando `dry-run` compila sem enviar o Worker. Os dois comandos seguintes
criam a tabela no D1 remoto e publicam este serviço, respectivamente. Nenhuma
chave privada ou token de usuário precisa ser configurado como secret no Worker.
Depois da publicação, uma leitura autenticada de empresa ainda vazia deve
retornar revisão zero; essa leitura não cria uma linha no banco.

Os testes usam criptografia RS256 real e SQLite em memória para exercitar o SQL
CAS, sem rede nem dados privados. O exemplo desativa logs e URLs de preview e não
define cron. Workers Free impõe seu limite de CPU de 10 ms; limites customizados
de CPU exigem plano pago e por isso não são configurados. Workers/D1 Free aplicam seus próprios limites;
o exemplo não altera o plano da conta. A leitura de um estado executa uma consulta
por chave primária. A gravação bem-sucedida executa um CAS; um conflito faz somente
uma leitura adicional da revisão. O tamanho máximo limita custo de validação e
armazenamento, mas orçamento real de CPU deve ser medido no ambiente Cloudflare.

Fontes primárias: [chaves públicas de service accounts](https://docs.cloud.google.com/iam/docs/service-account-creds),
[D1 prepared statements](https://developers.cloudflare.com/d1/worker-api/prepared-statements/)
e [limites Workers](https://developers.cloudflare.com/workers/platform/limits/).
