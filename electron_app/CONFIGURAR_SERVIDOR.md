# Configurar servidor do JK Sistema Cliente

O aplicativo abre o endereco configurado em `client-config.json`.

Na primeira execucao, o app copia essa configuracao para a pasta do usuario do Windows:

`%APPDATA%\JK Sistema Cliente\client-config.json`

Para trocar o servidor depois de instalado:

1. Feche o JK Sistema Cliente.
2. Abra o arquivo `%APPDATA%\JK Sistema Cliente\client-config.json`.
3. Altere o campo `appUrl`.
4. Abra o JK Sistema Cliente novamente.

Exemplo:

```json
{
  "appUrl": "https://jk-sistema-api-1077918177671.southamerica-east1.run.app/frontend_index.html"
}
```

Tambem e possivel iniciar o app com a variavel de ambiente `JK_APP_URL`.
