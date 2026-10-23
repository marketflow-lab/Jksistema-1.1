# JK Sistema authentication gateway

Serviço mínimo de autenticação para instalações desktop sem arquivos locais de usuários.
O restante do JK Sistema continua executando no backend local.

## Contrato de segurança

- O desktop envia somente usuário, senha, identificação da máquina, versão do app e um nonce aleatório.
- O nonce é incluído na assinatura para impedir o reaproveitamento da resposta.
- O `client_id` vem exclusivamente do documento central do usuário; ele não é aceito no request.
- A senha é validada no Cloud Run e nunca é registrada em log.
- O vínculo da máquina usa uma transação do Firestore para impedir corridas no limite de dispositivos.
- O serviço cria um Firebase ID token com hashes do perfil, permissões, política e máquina.
- O backend local verifica assinatura, projeto, emissor, audiência, provedor, idade e hashes antes de criar o token local.
- Nenhuma chave privada ou conta de serviço é distribuída no instalador. O Cloud Run usa identidade de serviço.
- A URL central vem do arquivo empacotado; o `client-config.json` editável do usuário não pode redirecionar credenciais.

## Configuração do serviço

Variáveis obrigatórias:

- `FIREBASE_PROJECT_ID=jkjkjk-485920`
- `FIREBASE_WEB_API_KEY=<chave web pública e restrita do projeto>`

Variáveis opcionais estão documentadas em `.env.example`. O serviço usa por padrão a coleção
`jk_sistema_usuarios` e exige no mínimo o app `1.0.124`.

A identidade do Cloud Run precisa apenas das permissões necessárias para ler/atualizar os usuários
no Firestore e assinar tokens Firebase. Conceda `roles/datastore.user` e a capacidade
`iam.serviceAccounts.signBlob` à identidade do serviço, preferencialmente pelo papel
`roles/iam.serviceAccountTokenCreator` aplicado à própria conta. Não crie nem baixe chave JSON.

## Ordem obrigatória de ativação

1. Ativar faturamento e as APIs necessárias somente após autorização explícita.
2. Criar uma conta de serviço exclusiva para `jk-auth-gateway` e aplicar os papéis mínimos.
3. Implantar `cloud/auth_gateway` no Cloud Run, região `southamerica-east1`, com acesso público ao endpoint.
4. Validar o endpoint direto `/health` e um login controlado sem registrar credenciais no terminal.
5. Publicar o rewrite de `firebase.json` no Firebase Hosting.
6. Confirmar `https://jkjkjk-485920.web.app/api/auth/v1/health` com `ok: true`.
7. Só então gerar e publicar a atualização do desktop.

O modo inicial do desktop é `prefer`: instalações antigas ainda podem usar a fonte legada se o endpoint
não existir ou estiver indisponível. Após o primeiro login central bem-sucedido, o app substitui a senha
local por um hash aleatório impossível de reutilizar, convergindo aquele usuário para a autoridade central.
Instalações limpas não possuem fonte legada e, portanto, dependem do gateway desde o primeiro login.

## Desenvolvimento e testes

Execute a partir da raiz do repositório:

```powershell
python -m pytest -q tests/test_remote_auth_client.py tests/test_auth_gateway_domain.py tests/test_auth_gateway_http.py tests/test_admin_usuarios_remote_auth.py
```

O container usa `cloud/auth_gateway/Dockerfile`. O arquivo `.env.example` é apenas um modelo e não deve
ser renomeado para um arquivo versionado com valores de produção.

## Autorização do Cofre por empresa (protocolo 1)

Clientes compatíveis enviam `X-JK-Cofre-Protocol: 1` no login e na troca de senha.
O gateway acrescenta `cofre_access: {protocol: 1, capability: <JWT>}` ao envelope
assinado somente para usuários com `full` ou `cofre`. Clientes sem esse cabeçalho
mantêm o hash e o contrato anteriores. O cabeçalho negocia também a chave de
permissão `cofre`, mesmo quando a versão declarada é anterior.

A capability RS256 usa a identidade e a permissão IAM `signBlob` já utilizadas
na autenticação, sem criar grant, consultar usuário novamente ou executar outro
sign-in Firebase. Claims: `iss` (email público da conta de serviço), `aud=jk-cofre`,
`sub`, `client_id`, `machine_hash`, `scope`, `iat`, `exp`. A validade termina em até
oito horas, limitada pela validade da conta e pelas sessões existentes. A leitura
requer `full || cofre`; a escrita requer também `full || admin_usuarios`.

O backend valida a assinatura e associa a capability ao token local apenas em
memória. As rotas Cofre usam essa sessão, sem consultar permissões, grants ou
presença no Firebase. A chave pública vem exclusivamente do endpoint JWK Google
da identidade confiável, com cache; ela nunca é escolhida por uma URL no JWT.
IAM pode emitir sem `kid`, caso em que o verificador tenta até dez chaves RSA do
conjunto confiável. Não se distribui chave privada nem refresh token ao Cofre.
O refresh Firebase existente não estende a capability; expiração e reinício do
backend encerram a autorização no protocolo 1, e a revogação remota não é imediata nesse modelo.

## Sessão única JK e recuperação do Cofre (protocolo 2)

O desktop novo preserva o cabeçalho de compatibilidade `X-JK-Cofre-Protocol: 1`
e envia também `X-JK-Cofre-Session-Protocol: 2`. O login normal do JK, por senha
ou Google, entrega no mesmo envelope assinado:
`cofre_access: {protocol: 2, capability, session_grant, session_expires_at}`.
O hash autenticado pelo ID token cobre a extensão inteira; identidade, empresa,
máquina, nonce, política e escopos são verificados antes de qualquer persistência.

A capability continua RS256, válida por até oito horas e verificada pelas chaves
públicas confiáveis do Google. O `session_grant` é uma credencial opaca para o
desktop: somente o gateway verifica sua assinatura HMAC com segredo estável de
servidor. O prazo absoluto termina em até 30 dias ou no vencimento da licença,
o que ocorrer primeiro; renovações não prorrogam esse prazo. Não se usa uma
assinatura IAM de 30 dias: as chaves públicas de `signBlob` só têm publicação
garantida por pelo menos 12 horas, insuficiente para a credencial longa
([documentação IAM](https://docs.cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/signBlob)).

O backend guarda credencial, contexto verificado e prazo no Credential Manager
do Windows, em blocos limitados, vinculados ao hash do token JK e à identidade.
Não há fallback para arquivo plaintext, localStorage ou senha salva. Ao reiniciar
ou se aproximar do vencimento da capability, valida a sessão JK existente e a
política local autenticada, que só pode restringir o escopo. Então chama
`POST /api/auth/v1/cofre/session`, que valida a credencial opaca e a máquina e
assina outra capability curta. Não consulta perfil, Firestore, grants Firebase,
presença nem Firebase Auth nesse caminho. Não repete automaticamente mutações.

O endpoint aceita somente `session_grant`, `machine_id`, `request_nonce` e
`app_version`; a resposta inclui `success`, `protocol`, o mesmo nonce, uma
capability nova, o mesmo grant e o mesmo `session_expires_at`. O desktop verifica
assinatura atual, identidade, escopo e prazo da capability antes de utilizá-la.
Concorrência compartilha uma renovação; indisponibilidade retorna 503 recuperável
sem apagar o login geral do JK. JWT JK inválido e negação real continuam 401/403.

Configure `JK_COFRE_SESSION_SECRET` exclusivamente no Cloud Run, com 32 bytes
aleatórios em 64 dígitos hexadecimais, injetados por versão fixa do Secret Manager.
Nenhum segredo real deve entrar no repositório, instalador ou resposta ao cliente.
Para uma rotação planejada, coloque a chave anterior em
`JK_COFRE_SESSION_PREVIOUS_SECRET` por pelo menos 30 dias; depois remova-a. Uma
revogação emergencial pode removê-la antes, invalidando os grants correspondentes.
Sem segredo configurado, o login mantém o protocolo 1 e a renovação fica indisponível.

O login Google usa `GOOGLE_LOGIN_CLIENT_ID`, a audiência pública já utilizada pelo
JK. O gateway verifica o ID token e encontra a conta na consulta normal do login,
sem aceitar empresa, usuário ou permissões fornecidos pelo desktop. A consulta de
entrada substitui a busca legada de usuários; abrir/renovar o Cofre não a repete.

Ativação: configurar o segredo e a audiência, implantar primeiro o gateway e
depois integrar o desktop aprovado. Não exige alteração do Worker/D1 nem de Rules
Firebase. Uma sessão antiga que já perdeu toda prova remota não pode receber
permissão de nuvem apenas por possuir um token local: o próximo login normal no
JK estabelece a nova credencial, sem um login separado no Cofre. O prazo de
30 dias também limita a defasagem de revogações remotas sem consulta online; a
política local atualizada pode restringir antes. Reativar acesso remoto além
desse prazo exige autenticação normal do JK.

## Conexão Firebase restrita após login (protocolo 1)

Clientes novos enviam `X-JK-Firebase-Protocol: 1` no login e na troca de senha.
Depois de validar usuário e máquina, o gateway inclui `firebase_access` no payload
assinado: projeto/chave web pública, ID de grant, ID token dedicado, refresh token,
validade do token e validade máxima da sessão. Clientes sem o cabeçalho mantêm o
contrato anterior. O refresh renova o ID token, mas não prorroga o grant de oito
horas, também limitado pela validade de acesso do usuário.

O grant `jk_sistema_firebase_grants/{id}` contém somente projeção pública do perfil,
permissões, máquinas e IDs dos recursos autorizados. Não contém senhas, hashes de
senha, tokens nem chave administrativa. As Rules conferem o grant e a política
atual do usuário a cada acesso. Alterações de empresa, permissões, máquinas,
validade, status ou `updated_at` invalidam a sessão antiga; os fluxos suportados de
troca de senha atualizam `updated_at`. Edições administrativas diretas também devem
atualizar esse campo. O usuário realiza novo login para obter a política atual.

O escopo inicial permite somente sincronização v2 entre máquinas do próprio
usuário, conforme suas permissões, e leitura da configuração da empresa. Não
libera coleção administrativa de usuários, compartilhamentos entre usuários,
credenciais centrais ou pacotes plaintext v1. Uma máquina recém-autorizada pode
exigir novo login nas outras máquinas para renovar a relação de membros e o
envelope criptográfico. A conexão automática não inicia importação de arquivos.

Antes da ativação, publicar conjuntamente as Rules versionadas e o gateway; esta
implementação não executa deploy. O status do desktop só confirma conexão após
ler seu grant pelas Rules. Testes de Rules usam o projeto de demonstração local:

```powershell
$env:FIRESTORE_EMULATOR_HOST = '127.0.0.1:8787'
python -m pytest -q tests/test_auth_gateway_firebase_sessions.py tests/test_auth_gateway_firebase_rules_emulator.py
```

O teste de integração desativa o atalho `Bearer owner` do SDK no cliente sob teste,
mantém o ID token restrito e exercita upload, criptografia, download e transação de
ponteiros pelos helpers reais. Dados sintéticos são semeados somente no emulador.
