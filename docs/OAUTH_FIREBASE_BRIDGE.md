# Ponte OAuth local pelo Firebase Hosting

O app local inicia o OAuth em `127.0.0.1:8001`, mas alguns provedores exigem
callback HTTPS publico. Para isso, o Firebase Hosting recebe o callback e
redireciona de volta para o backend local preservando `code`, `state` e erros.

## Links para cadastrar

Bling e Mercado Livre:

```text
https://jkjkjk-485920.web.app/auth/callback
```

Google OAuth local:

```text
https://jkjkjk-485920.web.app/auth/google/callback
```

Produção Cloud Run continua usando:

```text
https://jk-sistema-api-1077918177671.southamerica-east1.run.app/auth/callback
https://jk-sistema-api-1077918177671.southamerica-east1.run.app/auth/google/callback
```

## Deploy

Rode:

```bat
deploy_firebase_oauth_bridge.bat
```

Se o deploy falhar com `SERVICE_DISABLED`, ative a Cloud Resource Manager API no
projeto `jkjkjk-485920` e tente novamente:

```text
https://console.developers.google.com/apis/api/cloudresourcemanager.googleapis.com/overview?project=jkjkjk-485920
```

Depois do deploy, teste:

```text
https://jkjkjk-485920.web.app/auth/callback?test=1
```

Com o backend local aberto, essa URL deve redirecionar para:

```text
http://127.0.0.1:8001/auth/callback?test=1
```
