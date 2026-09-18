# Atualizacao automatica

O cliente desktop usa `electron-updater` com GitHub Releases e possui dois perfis de pacote.

- `app-update`: atualizacao comum e leve. Inclui Electron, codigo do `local_app` e o contrato `installer-runtime.lock.json`. Python, wheelhouse, Whisper e VC++ permanecem no computador e sao reutilizados somente depois da validacao dos hashes.
- `runtime-update`: instalador completo e offline. Inclui todos os componentes e deve ser usado quando Python, `requirements.txt`, wheelhouse, Whisper, VC++ ou o shell de instalacao mudarem.

O instalador completo mais recente permanece disponivel para instalacoes novas. Uma versao comum pode publicar somente o update leve; nesse caso, uma instalacao nova usa o instalador completo-base e recebe depois a atualizacao comum.

## Publicacao pelo GitHub Actions

1. Atualize a versao nos arquivos de release pelo fluxo normal.
2. Confirme que `installer-runtime.lock.json` continua compativel. Alteracoes nos componentes pesados exigem um novo contrato e o modo `runtime-update`.
3. Abra o workflow **Desktop release** e informe a tag correspondente, por exemplo `v1.0.149`.
4. Escolha `app-update` para uma versao que altera somente o aplicativo ou `runtime-update` para gerar o instalador completo.
5. Execute primeiro com `publish=false`. O GitHub gera, verifica e disponibiliza os artefatos internos por 14 dias.
6. Depois da revisao, execute com `publish=true`. A publicacao nasce como draft e somente fica publica depois do upload integral.

O workflow nao roda em `push` e nao publica sem a opcao explicita. A tag informada deve coincidir com a versao de `electron_app/package.json`.

## Builds locais de diagnostico

```powershell
npm.cmd --prefix electron_app run dist:update
npm.cmd --prefix electron_app run dist:full
```

`dist:update` nao prepara nem carrega o runtime pesado. `dist:full` restaura e valida o ambiente offline completo. Esses comandos usam `--publish never`.
O comando antigo `dist:publish` foi desativado para impedir build duplicado e publicacao parcial pela maquina local.

## Regras de seguranca

- O update leve nunca serve como instalacao inicial. Se o runtime local estiver ausente ou divergente, o instalador ou o primeiro boot solicita o instalador completo.
- `runtime_id` vincula a arvore Python, `requirements.txt`, manifesto das wheels, VC++ e manifesto Whisper.
- O codigo continua sendo materializado de forma transacional; dados em `info`, ContextVault, SKU, logs, `.venv` e demais dados persistentes sao preservados.
- `latest.yml` deve vir de somente um perfil por release. Versoes com mudanca de runtime publicam o `latest.yml` do instalador completo.
- Tokens nao entram no aplicativo. O workflow usa o token efemero do GitHub somente no job protegido de publicacao.
