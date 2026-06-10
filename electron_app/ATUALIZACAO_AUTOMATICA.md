# Atualizacao automatica

O cliente desktop usa `electron-updater` com GitHub Releases.

## Como publicar uma nova versao

1. Aumente a versao em `electron_app/package.json`, por exemplo de `1.0.5` para `1.0.6`.
2. Gere e publique a release:

```powershell
cd electron_app
$env:GH_TOKEN="SEU_TOKEN_DO_GITHUB_COM_PERMISSAO_DE_RELEASE"
npm.cmd run dist:publish
```

O `electron-builder` vai criar o instalador, o arquivo `latest.yml` e publicar tudo no repo:

`marketflow-lab/Jksistema-1.1`

## Como o app atualiza

- Ao abrir o app instalado, ele verifica se existe uma versao nova.
- Se existir, baixa automaticamente em segundo plano.
- Ao terminar, pergunta se o usuario quer reiniciar para instalar.

## Observacoes importantes

- O app instalado so detecta versoes publicadas em GitHub Releases.
- Apenas mudar arquivos/commits no GitHub nao dispara atualizacao automatica.
- Nao coloque tokens de API dentro do app para atualizacao. Use sempre o canal normal de GitHub Releases ou um servidor proprio.
- Dados locais da pasta `info` nao sao sobrescritos pelo atualizador.
