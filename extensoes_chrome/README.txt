Coloque aqui extensoes do Chrome descompactadas para o Electron carregar ao iniciar.

Formato esperado:

extensoes_chrome/
  mercado_turbo/
    manifest.json
    background.js
    content.js
    ...

Observacoes:
- O Electron nao instala extensoes direto pela Chrome Web Store.
- A extensao precisa estar descompactada em uma pasta com manifest.json.
- Ao reiniciar o aplicativo, o Electron carrega as extensoes desta pasta na sessao do Mercado Livre.
- Extensoes que dependem de APIs exclusivas do Chrome podem nao funcionar no Electron.
- Se preferir outra pasta, defina a variavel de ambiente JK_CHROME_EXTENSIONS_DIR com o caminho.
