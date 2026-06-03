# JK Sistema Android

Cliente Android privado do JK Sistema.

Este aplicativo carrega os módulos Estoque, Cadastro, Perguntas, Integração e Chat em uma WebView e reutiliza o backend do sistema pela rede. Isso preserva o app desktop como está e evita misturar Android com os módulos Electron/Python.

## Limite importante

O Android não executa diretamente os módulos que dependem de Electron, Chrome desktop, extensão AvantPro, BAT ou automação de navegador desktop. Para usar o sistema completo, o backend JK Sistema precisa estar acessível em um servidor ou em uma máquina da rede.

## Configuração

Edite `app/src/main/assets/jk_android_config.json` antes de gerar o APK:

```json
{
  "appBaseUrl": "http://IP-DO-SERVIDOR:8001/frontend_index.html",
  "updateManifestUrl": "https://seu-servidor.com/jk-android-update.json",
  "allowUserServerEdit": true
}
```

Se `appBaseUrl` ficar vazio, o app pede o endereço ao abrir.

Se `updateManifestUrl` ficar vazio, o app tentará consultar:

```text
/api/mobile-update/check?platform=android&version=<versao_atual>
```

no mesmo servidor configurado.

## Atualização

O Android baixa o novo APK e abre a tela de instalação. Para instalação totalmente silenciosa, é necessário distribuir por Play Store gerenciada, MDM ou modo dispositivo corporativo. APK privado comum exige confirmação do Android.

Use `update-manifest.example.json` como modelo do manifesto de atualização.
