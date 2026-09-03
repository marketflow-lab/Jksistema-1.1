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
