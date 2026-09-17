# Validacao da versao 1.0.144

Data: 17 de setembro de 2026.

## Resultado

A versao 1.0.144 foi preparada e validada em worktree dedicado a partir da candidata integrada `0a1dd96`.

- Os tres gates de regressao do workflow de desktop foram aprovados: perfis de release, materializacao do runtime e inicializacao do backend.
- A remocao do quadro "Atalhos de decisao" passou na verificacao de sintaxe e no teste de navegador do modulo Perguntas.
- Os contratos de versao, hashes publicos da IA e mensagens de recuperacao do historico foram reconciliados e aprovados em testes direcionados.
- Os 158 testes direcionados de identidade de conversa, continuidade do BlackJohn e ponte WhatsApp foram aprovados fora do sandbox depois que a execucao restrita bloqueou os diretorios temporarios do aplicativo.
- O gateway passou na verificacao TypeScript e em 3 arquivos com 70 testes Vitest.
- O contrato seguro do Bug Hunter teve 1 teste aprovado e 1 ignorado conforme o ambiente; as 5 sondas diagnosticas foram aprovadas sem chamadas externas.
- A primeira varredura Python executou 5.897 casos: 5.869 foram aprovados, 26 ignorados e 2 exigiram reconciliacao ou repeticao; ambos passaram na repeticao direcionada.
- Uma segunda varredura Python sob sandbox aprovou 5.850 casos e concentrou 21 bloqueios em tres grupos que escrevem no diretorio temporario do aplicativo; esses mesmos grupos somaram 158 testes aprovados fora do sandbox.
- Nas regressoes Node e Electron, 138 de 140 arquivos foram aprovados. Permanecem o limite arquitetural anterior de 400 linhas em `static/cadastro/main/06-importacoes-catalogos.js` e o teste exclusivo do instalador completo, que requer `dist-client-setup` e nao se aplica ao perfil `app-update`.
- O pacote leve foi construido com 1.053 arquivos gerenciados, sem bytecode ou caches indevidos, e passou na verificacao de fonte e do conteudo empacotado.
- O contrato imutavel do runtime foi preservado com o identificador `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Artefatos locais de validacao

| Arquivo | Tamanho | SHA-256 |
| --- | ---: | --- |
| `JK-Sistema-Cliente-Update-1.0.144.exe` | 92.011.066 bytes | `943263EBFC9A37DDFF30D367446C735C0CE50BC9FBAC389415E0DEC848C16661` |
| `JK-Sistema-Cliente-Update-1.0.144.exe.blockmap` | 97.063 bytes | `299F40573AB2CBADD290DC74713194C3D7D28CF7CB5F0DEE63F562C4C3627D1F` |
| `latest.yml` | 372 bytes | `EE80B47D772C17B985AD66B4F62EC4E4CCABA60EE38E952D8037AD5075F3B8F7` |

Os artefatos oficiais sao reconstruidos pelo GitHub Actions a partir do commit publicado e recebem hashes proprios no arquivo `SHA256SUMS.txt` da release.

## Escopo operacional

A validacao gerou artefatos somente no worktree de release. Nenhuma copia instalada do aplicativo, servico em execucao ou dado operacional de loja foi atualizado.
