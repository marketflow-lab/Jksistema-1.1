# Validação da versão 1.0.143

Data: 17 de setembro de 2026.

## Resultado

A versão 1.0.143 foi compilada e validada no worktree dedicado `codex/release-v1.0.143`.

- Suite Python completa: 5.864 testes aprovados, 26 ignorados e 119 avisos; cobertura total de 60,72%.
- Casos direcionados do agente unificado, recuperação de pesquisa e versão: 168 testes e 83 subtestes aprovados.
- Regressão dos grupos de IA ajustados: 245 testes aprovados.
- Regressão de replay, fila e limites arquiteturais: 23 testes aprovados.
- Regressão de autenticação e isolamento de estado Firebase: 46 testes aprovados.
- Regressão do worker de indexação e sessão Firebase: 29 testes aprovados.
- Gateway: verificação TypeScript aprovada; 3 arquivos e 70 testes Vitest aprovados.
- Contrato Bug Hunter: 1 teste aprovado e 1 ignorado conforme o ambiente.
- Sondas diagnósticas: 5 de 5 aprovadas.
- Manifesto do Context Bundle, dependências e contrato do pacote Electron aprovados.
- Pacote final: 1.052 arquivos gerenciados, sem bytecode ou caches indevidos.
- Atualização empacotada: migração simulada de 1.0.99 para 1.0.143 aprovada.
- Instalação offline: Python incorporado, 127 wheels com hash, `pip check`, imports críticos, backend e idempotência aprovados.
- Verificação profunda do runtime e do instalador aprovada.

Na regressão Node/Electron, 137 de 139 arquivos foram aprovados na execução anterior à montagem do pacote. O teste de atualização empacotada foi repetido depois da montagem e aprovado. A única pendência restante é o limite arquitetural já existente de `static/cadastro/main/06-importacoes-catalogos.js`, que excede 400 linhas e não pertence às alterações desta versão.

## Artefatos

| Arquivo | Tamanho | SHA-256 |
| --- | ---: | --- |
| `JK-Sistema-Cliente-Setup-1.0.143.exe` | 943.164.595 bytes | `2FB8C96EE57B4A81851812343D93D207A8DFDCDE37BABE8D0184C17D88905E66` |
| `JK-Sistema-Cliente-Setup-1.0.143.exe.blockmap` | 977.205 bytes | `F333B01D0806A58D18F52689E06DC22E7A33C1760806D71D77D3F12E8D86C2C9` |
| `latest.yml` | 371 bytes | `68F8A4614B537A36BE7268F459A6202B07914E7E6A49F1FB2B89DD8D1438A513` |

O SHA-512 do instalador foi recalculado e corresponde ao valor publicado em `latest.yml`.

## Escopo operacional

O processo gerou os artefatos de distribuição e validou o fluxo offline e de atualização em ambiente de teste. Nenhuma cópia instalada do aplicativo, serviço em execução ou dado de loja foi alterado durante a validação.
