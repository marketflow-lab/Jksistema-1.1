# Git Tree Audit - 2026-07-08

Contexto: saneamento da arvore Git para preparar release sem misturar codigo, dados locais e artefatos temporarios.

## Snapshot

- Total aproximado de itens alterados: 2223.
- Por status: 1822 deleted, 300 untracked, 101 modified.
- Maiores grupos: backups, backend, info, static, electron_app.
- Commit IA/Codex SQLite ja realizado: e828cb6.

## Decisoes

- Nao usar `git add -A`.
- Commits devem ser seletivos e por intencao.
- `info/**` e caches locais devem ser preservados no disco, mas nao entrar em commits salvo fixture explicita.
- `backups/**` versionados removidos do disco devem sair do Git em commit proprio.
- Temporarios, screenshots, extrações de instalador, caches e anexos locais ficam fora do Git.

## Ordem De Trabalho

1. Remover do Git os backups versionados ja deletados.
2. Versionar modularizacao backend apos validacao de sintaxe/imports.
3. Versionar pares frontend/static apos checagem de espelhos.
4. Versionar Electron/package/runtime necessario para empacotamento.
5. Versionar testes/scripts uteis.
6. Revisar rastreamento de `info/**` separadamente, sem apagar dados locais.

## Guardrails

- Conferir `git diff --cached --name-status` antes de cada commit.
- Bloquear commit se `info/**` sensivel aparecer staged por acidente.
- Manter dados locais e backups nao versionados intactos, exceto quando ja estavam deletados e a acao for apenas remover do indice do Git.
