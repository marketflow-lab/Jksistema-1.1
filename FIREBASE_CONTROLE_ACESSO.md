# Controle de acesso pelo Firebase

O app agora pode usar o Firebase Firestore como base central de usuarios,
permissoes de modulos e maquinas autorizadas.

## Como ativar

1. Instale as dependencias do backend:

```powershell
pip install -r requirements.txt
```

2. No Firebase Console, crie um projeto e habilite o Firestore.

3. Crie uma chave de conta de servico e salve como:

```text
info/firebase-service-account.json
```

No instalador padrao, a chave e as variaveis de presenca acompanham o pacote
normal do aplicativo. O app local ativa o Firebase automaticamente na proxima
abertura quando encontrar esses arquivos.

Tambem e possivel apontar por variavel de ambiente:

```powershell
$env:FIREBASE_SERVICE_ACCOUNT_FILE="C:\caminho\firebase-service-account.json"
```

4. Ative o Firebase como autoridade de acesso:

```powershell
$env:JK_ACCESS_BACKEND="firebase"
$env:FIREBASE_PROJECT_ID="seu-projeto-firebase"
```

Se quiser deixar em modo automatico, use:

```powershell
$env:JK_ACCESS_BACKEND="auto"
```

Nesse modo, o app usa Firebase quando estiver configurado e cai no controle
local atual se o Firebase ainda nao estiver pronto.

Os launchers `iniciar_servidor.bat`, `Executar.bat` e o app Electron instalado
ja deixam `JK_ACCESS_BACKEND=auto` por padrao e mudam para `firebase` quando
encontram a chave privada em `info/` ou na raiz local do app.

Para conferir o status pelo backend:

```text
GET /api/admin/access-backend
```

## Colecao usada

Padrao:

```text
jk_sistema_usuarios
```

Para trocar:

```powershell
$env:FIREBASE_USERS_COLLECTION="minha_colecao_de_usuarios"
```

## Campos de cada usuario

Cada documento deve ter como ID o login do usuario em letras minusculas.

Exemplo de documento:

```json
{
  "username": "caio",
  "password_hash": "$2b$12$...",
  "name": "Caio Saldanha",
  "email": "email@empresa.com",
  "client_id": "000002",
  "active": true,
  "valid_until": "31/12/2026",
  "max_machines": 1,
  "machine_ids": [],
  "permissions": {
    "favoritos": true,
    "vendas": true,
    "configuracoes": false,
    "full": false
  }
}
```

O painel `Configuracoes > Usuarios` continua funcionando. Quando o Firebase
estiver ativo, criar, editar, bloquear, resetar maquinas e alterar permissoes
salva no Firestore e mantem um cache local.

## Bootstrap inicial

Quando a colecao estiver vazia, o app pode copiar os usuarios locais atuais
para o Firebase automaticamente. Esse comportamento fica ativo por padrao.

Para desativar:

```powershell
$env:FIREBASE_SEED_LOCAL_USERS="false"
```

## Auditoria

Logins tambem podem ser gravados na colecao:

```text
jk_sistema_login_audit
```

Para trocar:

```powershell
$env:FIREBASE_LOGIN_AUDIT_COLLECTION="minha_auditoria"
```

## Maquinas online

O app envia um heartbeat enquanto o usuario esta logado. No sidebar do usuario,
o dashboard mostra as maquinas online do mesmo login.

Padrao de online: heartbeat recebido nos ultimos 150 segundos.

Para ajustar:

```powershell
$env:JK_MACHINE_ONLINE_TIMEOUT_SECONDS="300"
```

Com Firebase ativo, os heartbeats ficam na colecao:

```text
jk_sistema_machine_presence
```

Para trocar:

```powershell
$env:FIREBASE_MACHINE_PRESENCE_COLLECTION="minha_presenca_maquinas"
```
