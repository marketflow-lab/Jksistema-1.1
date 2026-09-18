# Validacao da versao 1.0.147

Data: 18 de setembro de 2026.

## Resultado

A versao 1.0.147 foi preparada a partir das correcoes aprovadas `be99586` e `680b412` sobre a release 1.0.146.

- A regressao ampliada do fluxo Codex e de Perguntas e pos-venda aprovou 746 testes Python.
- A regressao focal do transporte, da persistencia de threads e da preservacao das respostas aprovou 49 testes com o runtime legado e com o SDK empacotado.
- Uma chamada real do SDK 0.154.0 com o executavel Codex atual terminou com estado `completed` e resposta valida.
- A integridade da wheel empacotada foi confirmada pelo SHA-256 `b5f354e1280621d0f5e28313ecf6974d2a98bcf0b088dbe791dc6df5044d2214`.
- Os contratos de versao e sincronizacao foram aprovados com a versao canonica 1.0.147.
- Os contratos de dependencias e os tres gates do workflow de desktop foram aprovados: perfis de release, materializacao do runtime e inicializacao do backend.
- O pacote leve foi construido com 1.054 arquivos gerenciados, sem bytecode ou caches indevidos, e passou nas verificacoes de fonte e do conteudo empacotado.
- O contrato imutavel do runtime foi preservado com o identificador `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Artefatos locais de validacao

| Arquivo | Tamanho | SHA-256 |
| --- | ---: | --- |
| `JK-Sistema-Cliente-Update-1.0.147.exe` | 92.111.782 bytes | `e53f40dd13b2cf4cdc01069c6dcacd734c36d9b0847151b722e629f2363cf328` |
| `JK-Sistema-Cliente-Update-1.0.147.exe.blockmap` | 97.379 bytes | `74809bbf1e8167a26095539591e5d00fc6473ac5f04174eabae66415b812d78f` |
| `latest.yml` | 372 bytes | `d503af9948d14c75ffac526c2dde7eb83cd42eb13508a31db41024a43647ea63` |

Os artefatos oficiais sao reconstruidos pelo GitHub Actions a partir do commit publicado e recebem hashes proprios no arquivo `SHA256SUMS.txt` da release.

## Escopo operacional

A validacao gerou artefatos somente na area de desenvolvimento. Nenhuma copia instalada do aplicativo, servico em execucao ou dado operacional de loja foi atualizado.
