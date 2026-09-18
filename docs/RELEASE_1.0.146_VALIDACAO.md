# Validacao da versao 1.0.146

Data: 18 de setembro de 2026.

## Resultado

A versao 1.0.146 foi preparada em worktree dedicado a partir das alteracoes aprovadas `c2f9580` e `3e2305a` sobre a release 1.0.145.

- A suite Python completa da correcao de geracao da IA terminou sem falhas; 26 casos dependentes de servicos locais foram ignorados conforme seus contratos.
- A regressao conjunta no worktree de release aprovou 345 testes Python direcionados, o contrato persistente do frontend e o navegador sintetico do modulo Perguntas.
- O navegador confirmou cards com larguras diferentes conforme o nome, quebra automatica de linha e ausencia de rolagem horizontal em 1.365 px e 360 px.
- Os espelhos HTML, a sintaxe dos JavaScript alterados e a compilacao dos modulos Python foram aprovados.
- Os contratos de versao e sincronizacao foram aprovados com a versao canonica 1.0.146.
- Os tres gates do workflow de desktop foram aprovados: perfis de release, materializacao do runtime e inicializacao do backend.
- O pacote leve foi construido com 1.053 arquivos gerenciados, sem bytecode ou caches indevidos, e passou nas verificacoes de fonte e do conteudo empacotado.
- O contrato imutavel do runtime foi preservado com o identificador `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Artefatos locais de validacao

| Arquivo | Tamanho | SHA-256 |
| --- | ---: | --- |
| `JK-Sistema-Cliente-Update-1.0.146.exe` | 92.014.554 bytes | `9ca60dec561c95ffdc2d6e0159857ca9233bb18735d42887a3d64d4cde6a0da6` |
| `JK-Sistema-Cliente-Update-1.0.146.exe.blockmap` | 97.045 bytes | `456946406ca1c29bf2f6df921560093cb8219b1d11ec1f140833564d04816a47` |
| `latest.yml` | 372 bytes | `96918e578435b9b157eca46f379076aacd1f4f510c4a763613c492d1a92d903d` |

Os artefatos oficiais sao reconstruidos pelo GitHub Actions a partir do commit publicado e recebem hashes proprios no arquivo `SHA256SUMS.txt` da release.

## Escopo operacional

A validacao gerou artefatos somente no worktree de release. Nenhuma copia instalada do aplicativo, servico em execucao ou dado operacional de loja foi atualizado.
