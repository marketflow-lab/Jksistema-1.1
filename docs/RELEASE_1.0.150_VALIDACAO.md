# Validacao da versao 1.0.150

Data: 18 de setembro de 2026.

## Base e escopo

Release preparada sobre a `v1.0.149` publicada, incorporando os commits aprovados da correcao sistemica de bloqueios de lojas e a compatibilidade de conexoes Bling antigas encontrada durante a regressao.

O perfil permanece `app-update`. Dependencias, runtime Python e contrato do instalador nao mudaram. Runtime preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Verificacoes locais

- 779 testes Python integrados aprovados; 4 ignorados somente por falta de privilegio para links simbolicos no Windows.
- 127 testes direcionados aprovados para SharedSync, preservacao de credenciais, rollback e ciclo de vida de lojas.
- Casos de captura imutavel confirmam liberacao das travas antes de hashes, deltas, ZIP e processamento do Context Hub.
- Leitura operacional concluiu em 3,52 a 8,16 ms enquanto outro processo manteve a trava por mais de 10 segundos; o caminho legado atingiu o timeout de aquisicao.
- Renovacao concorrente, troca de conexao, revalidacao antes de escritas externas, falha parcial da automacao e preservacao de envio confirmado foram validadas.
- Preferencia de aprovacao, envio manual, automacao e identidade de lojas passaram nos contratos frontend.
- Gates de perfis de release, materializacao do runtime, bootstrap do backend, dependencias e pacote de atualizacao foram aprovados.
- Compilacao dos Python alterados e `git diff --check` aprovados.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.150`, `release_mode=app-update` e `publish=true`.

O workflow valida o pacote, gera `SHA256SUMS.txt` e publica os artefatos a partir de um draft completo. Nenhuma atualizacao da copia instalada faz parte deste procedimento.
