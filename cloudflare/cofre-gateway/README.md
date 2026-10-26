# Cofre de Informações: gateway por empresa

Worker e banco D1 próprios. Não depende do gateway WhatsApp, Firebase, Firestore,
filas ou cron. Anexos usam opcionalmente o namespace KV existente `MEDIA`, com
prefixo exclusivo do Cofre. Texto funciona sem esse binding. O endereço previsto é
`https://jk-cofre-gateway.jk-sistema.workers.dev`.

O processo de desenvolvimento deste pacote não cria recursos e não publica o
Worker. A configuração versionada é somente um exemplo; o identificador real do
D1 fica em `wrangler.toml`, ignorado pelo Git. O banco precisa ser exclusivo deste
serviço. A migração `0001` cria o snapshot por empresa; `0002` adiciona autoria,
tombstones de canais, recibos idempotentes, metadados e cotas de anexos.

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
  transação D1 com um fence aleatório de servidor. Estado e metadados mudam juntos.
  Em concorrência, somente uma gravação vence; as outras
  recebem `409 {error:"revision_conflict",revision:ATUAL}` e devem reler/conciliar.

Não há parâmetro de empresa. A chave D1 vem exclusivamente de `client_id` no JWT.
Campos ou parâmetros extras são rejeitados. A coluna `state` contém o último
snapshot; o serviço não faz mesclagem silenciosa nem mantém histórico de versões.
Exclusões recuperáveis são os registros de mensagem com `deleted:true` no estado.
O cabeçalho opcional `X-JK-Cofre-Metadata: 1` no GET acrescenta somente fora de
`state`: `metadata:{version:1,ownership:[{guild_id,channel_id,message_id,owner_username}],deleted_channels:[{guild_id,channel_id}]}`.
O GET sem cabeçalho continua com exatamente `revision,state`, filtrando canais
excluídos; o snapshot v1 não ganha campos novos. Canais excluídos continuam íntegros
na base e no GET opt-in, ocultos por `deleted_channels` nos clientes novos. PUTs
antigos podem omitir esses canais: o servidor reincorpora sua cópia canônica,
preservando histórico e tombstone enquanto permite editar os canais visíveis.
Para preservar edições confirmadas durante futuras importações, canais e
mensagens também aceitam `edited_fields`: uma lista sem repetição das chaves
cujos valores efetivos devem recriar os overlays locais. Mensagens aceitam no
máximo quatro itens: `content`, `author`, `links`, `embeds`. Canais aceitam no
máximo três: `name`, `category`, `topic`. A lista vazia é o padrão; omitir o campo
tem o mesmo significado. O Worker preserva a lista e os valores do snapshot;
a exceção é a recomposição dos canais tombstonados canônicos em PUTs que os
omitem ou trazem uma versão antiga. A flag de interface `edited` continua proibida.

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

O snapshot serializado e os metadados resultantes, em UTF-8, têm limite individual
de **1 MiB**. A requisição JSON inteira tem limite de **2 MiB + 64 KiB**, inclusive
quando não declara `Content-Length`; o teto do snapshot v1 permanece em 1 MiB.
Identificadores, URLs, tipos, duplicação de IDs e limites de coleções são validados
antes de qualquer gravação. O D1 repete o limite de tamanho por uma restrição SQL.

## Autenticação

O backend emite JWT RS256 com cabeçalho `alg:"RS256",typ:"JWT"` e `kid` opcional.
Os únicos claims aceitos são `iss,aud,sub,client_id,machine_hash,scope,iat,exp`.
`aud` precisa ser `jk-cofre`, `iss` precisa coincidir com `COFRE_ISSUER_EMAIL`,
`machine_hash` tem 64 caracteres hexadecimais e o prazo máximo é de oito horas.
JWT expirado é rejeitado. Aceita-se desvio de até 60 segundos apenas no `iat`.

Leitura exige `cofre:read`. Publicação PUT exige `cofre:read` e `cofre:write`; a decisão
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

## Publicações e autoria

`POST /v1/mutations` recebe schema fechado:

```json
{"request_id":"00000000-0000-4000-8000-000000000001","expected_revision":1,"action":"message.create","guild_id":"server1","channel_id":"channel1","content":"Texto","links":[{"url":"https://example.test"}],"asset_ids":[]}
```

As ações são `message.create`, `message.delete`, `message.restore`, `channel.delete`.
Delete/restore recebem `message_id`; conteúdo, links e até cinco `asset_ids` únicos
pertencem somente a create. Novo conteúdo aceita até 32.000 caracteres e 100 links;
os limites do snapshot legado são preservados. `request_id` é UUID4 minúsculo. ID nativo
`jk_msg_<UUIDhex32>`, autor inicial e horário UTC vêm do servidor. A resposta é
`{revision,state,metadata,message_id?}`. Repetir o mesmo request_id e a mesma ação
é idempotente, inclusive depois de um conflito concorrente; trocar ator ou dados
com o mesmo UUID recebe 409. O recibo não armazena cópias do acervo.

Qualquer leitor autenticado pode postar e excluir/restaurar suas próprias mensagens.
Somente `cofre:write` pode excluir canais e mensagens de outros usuários. O texto
do campo visual `author`, mesmo após edição administrativa, nunca determina
propriedade. Uma mensagem Discord sem registro de autoria JK é administrável
somente por `cofre:write`. Nenhuma identidade vem do corpo da mutation.

O PUT administrativo preserva proprietários existentes, mensagens nativas e
tombstones. Um snapshot antigo que perderia dados nativos ou reativaria mensagem
excluída recebe 409; restauração explícita usa mutation. Para a primeira publicação
de mensagens feitas localmente, o administrador pode atestar
`metadata:{version:1,ownership:[...],deleted_channels:[...]}`. Cada referência deve
existir no snapshot, e owner só é permitido em IDs nativos. O administrador é a
autoridade desse bootstrap; nunca se extrai propriedade do nome exibido. Owner
existente não pode mudar. Importadores de manifesto devem ignorar autoria externa;
o backend publica apenas a tabela de autoria JK validada. O snapshot continua
editável pelo administrador, incluindo o nome visual do autor.

## Anexos privados

1. `POST /v1/assets` recebe `{request_id,guild_id,channel_id,filename,size,sha256}`.
   Reserva espaço global e retorna `{asset_id,url,filename,content_type,size,sha256,ready}`.
   O ID tem 64 caracteres hexadecimais; o hash também é SHA256 hexadecimal minúsculo.
   O uploader precisa ler o canal ativo. Administradores podem preparar anexos de
   canais locais antes de publicar o primeiro snapshot.
2. `PUT /v1/assets/{asset_id}/content` recebe bytes brutos do uploader. Tamanho e
   SHA256 precisam coincidir com a reserva. MIME vem da assinatura dos bytes
   (PNG/JPEG/GIF/WebP), com fallback `application/octet-stream` para outros arquivos,
   inclusive PDF; extensão e
   Content-Type enviados pelo cliente não concedem confiança. Upload concluído
   pode ser repetido sem nova gravação KV. Concorrência usa lease no D1.
3. `GET /v1/assets/{asset_id}/{filename}` exige JWT de leitura da empresa e um
   vínculo com mensagem ativa em canal ativo. Antes de publicar, somente o uploader
   acessa sua reserva. Depois da primeira vinculação, exclusão remove acesso,
   inclusive do uploader, e preserva os bytes para restauração. A consulta D1 ocorre
   antes de qualquer KV.get, que usa stream. A resposta inclui `X-Cofre-SHA256`,
   `Content-Length`, `nosniff`, `no-store` e `Content-Disposition: attachment`.

URLs são canônicas no origin do pedido, sem token nem query. IDs de empresa e
usuário vêm somente do JWT. Anexos pertencem à empresa e ao canal reservados.
O máximo por arquivo é **10 MiB**, até **cinco** por novo post. O orçamento do
Cofre é **350 MiB globais**, contabilizados atomicamente no D1, até **250 novas
reservas por dia UTC** e, em contador independente, até **250 tentativas de escrita
KV por dia UTC**, incluindo tentativas que falham. Repetir uma reserva idempotente
não consome outra reserva diária. O limite
não amplia o plano Cloudflare: quotas de conta/namespace continuam compartilhadas
com usos existentes e podem ser atingidas antes. Não há R2 nem BLOB no D1.

O prefixo exclusivo é `cofre:v1:<sha256(client_id)>:<asset_id>`; o Worker nunca
lista o namespace, consulta dados WhatsApp ou remove outras chaves. Reservas
órfãs sem vínculo, com mais de 24 horas, são recolhidas em no máximo duas por
nova reserva. Espaço só é liberado após apagar a chave exata com sucesso. Uploads
em estado indeterminado conservam sua reserva até conclusão/repetição segura.
Mensagens/canais excluídos preservam anexos referenciados. Falhas KV mantêm reserva;
KV ausente ou indisponível recebe 503. Leitura ainda não propagada recebe 503 com
`Retry-After: 60`; KV é eventualmente consistente, inclusive para resultados
negativos. Não há polling, cron ou reenvio automático no Worker.

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
aplicam as migrações no D1 remoto e publicam este serviço, respectivamente.
Aplicar `0002` antes do código novo é obrigatório; o código anterior tolera as
tabelas e coluna extras. Configure o binding opcional MEDIA no arquivo local
usando o namespace existente autorizado, sem alterar o gateway WhatsApp. Nenhuma
chave privada ou token de usuário precisa ser configurado como secret no Worker.
Depois da publicação, uma leitura autenticada de empresa ainda vazia deve
retornar revisão zero; essa leitura não cria uma linha no banco.

Os testes usam criptografia RS256 real e SQLite em memória para exercitar o SQL
CAS, sem rede nem dados privados. O exemplo desativa logs e URLs de preview e não
define cron. Workers Free impõe seu limite de CPU de 10 ms; limites customizados
de CPU exigem plano pago e por isso não são configurados. Workers/D1 Free aplicam seus próprios limites;
o exemplo não altera o plano da conta. A leitura v1 executa uma consulta por chave
primária, mais a leitura de tombstones quando há snapshot alterado; 304 evita essa
consulta adicional. A leitura com metadata usa três consultas na mesma transação. A publicação
usa CAS e gravações auxiliares guardadas pelo fence. Não há consultas Firebase.
O tamanho máximo limita custo de validação e
armazenamento, mas orçamento real de CPU deve ser medido no ambiente Cloudflare.

Fontes primárias: [chaves públicas de service accounts](https://docs.cloud.google.com/iam/docs/service-account-creds),
[D1 prepared statements](https://developers.cloudflare.com/d1/worker-api/prepared-statements/)
e [limites Workers](https://developers.cloudflare.com/workers/platform/limits/).
Limites de armazenamento e consistência: [KV](https://developers.cloudflare.com/kv/platform/limits/),
[consistência KV](https://developers.cloudflare.com/kv/concepts/how-kv-works/),
[D1](https://developers.cloudflare.com/d1/platform/limits/) e
[transações batch D1](https://developers.cloudflare.com/d1/worker-api/d1-database/).
