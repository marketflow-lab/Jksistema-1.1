# Servidor local do JK Sistema Cliente

O aplicativo agora inicia o backend local automaticamente e abre:

```text
http://127.0.0.1:8001/frontend_index.html
```

Na primeira execucao, o app copia essa configuracao para a pasta do usuario do Windows:

`%APPDATA%\JK Sistema Cliente\client-config.json`

Normalmente nao e preciso trocar esse arquivo. Para usar um endereco personalizado depois de instalado:

1. Feche o JK Sistema Cliente.
2. Abra o arquivo `%APPDATA%\JK Sistema Cliente\client-config.json`.
3. Altere o campo `appUrl`.
4. Abra o JK Sistema Cliente novamente.

Exemplo:

```json
{
  "appUrl": "http://127.0.0.1:8001/frontend_index.html"
}
```

Tambem e possivel iniciar o app com a variavel de ambiente `JK_APP_URL`.

O codigo local do backend fica em `%APPDATA%\JK Sistema Cliente\local_app`.
As credenciais importadas ficam nessa mesma raiz, principalmente em `local_app\info`.
