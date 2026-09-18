# Validacao da versao 1.0.145

Data: 18 de setembro de 2026.

## Resultado

A versao 1.0.145 foi preparada e validada em worktree dedicado a partir do ajuste aprovado `a68c5df` sobre a release 1.0.144.

- Os tres gates de regressao do workflow de desktop foram aprovados: perfis de release, materializacao do runtime e inicializacao do backend.
- O teste de navegador do modulo Perguntas foi aprovado com o layout atualizado.
- A medicao geometrica confirmou os quadros ao lado direito e alinhados com o titulo em 1033 px; em 800 px e 360 px eles ficam abaixo da pergunta, sem rolagem horizontal.
- Os contratos de versao, do runtime e dos dois HTMLs espelhados foram aprovados em 7 testes direcionados.
- O pytest usou um diretorio temporario dentro do worktree porque o diretorio temporario global do Windows recusou acesso no sandbox.
- O pacote leve foi construido com 1.053 arquivos gerenciados, sem bytecode ou caches indevidos, e passou nas verificacoes de fonte e do conteudo empacotado.
- O contrato imutavel do runtime foi preservado com o identificador `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Artefatos locais de validacao

| Arquivo | Tamanho | SHA-256 |
| --- | ---: | --- |
| `JK-Sistema-Cliente-Update-1.0.145.exe` | 92.011.151 bytes | `e279f2928d4b3507428de1bb2c48328a0a82d4d45e81f44b6fa9a44eaaa2ffe6` |
| `JK-Sistema-Cliente-Update-1.0.145.exe.blockmap` | 97.041 bytes | `d57b87964406d0faa1f49961aa528a7a7f0556681b74dd641822816d8490a9f4` |
| `latest.yml` | 372 bytes | `6be413c1d743eb04e24d528c254d054135c6277314cd62e963dc3e3dd1c98fb9` |

Os artefatos oficiais sao reconstruidos pelo GitHub Actions a partir do commit publicado e recebem hashes proprios no arquivo `SHA256SUMS.txt` da release.

## Escopo operacional

A validacao gerou artefatos somente no worktree de release. Nenhuma copia instalada do aplicativo, servico em execucao ou dado operacional de loja foi atualizado.
