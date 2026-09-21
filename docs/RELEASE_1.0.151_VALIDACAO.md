# Validacao da versao 1.0.151

Data: 21 de setembro de 2026.

## Base e escopo

Release preparada sobre a `v1.0.150` publicada, incorporando a alteracao aprovada que mostra todas as variacoes, seus SKUs e o estoque atual quando a pergunta nao informa uma variacao especifica.

O perfil permanece `app-update`. Dependencias, runtime Python e contrato do instalador nao mudaram. Runtime preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Verificacoes locais

- Integracao no navegador confirmou nome, SKU e estoque atual de todas as variacoes, inclusive estoque zero.
- Carregamento, recuperacao, envio manual, atualizacao da lista e escopo de SKU do modulo Perguntas foram aprovados.
- 178 testes Python direcionados aos contratos de distribuicao e SharedSync foram aprovados; 2 foram ignorados por falta de permissao para junction ou link simbolico no Windows.
- Locks de 4 dependencias Node, 127 pacotes Python de runtime e 7 pacotes Python de teste foram conferidos.
- Os 24 espelhos HTML estaticos foram conferidos sem alteracoes.
- Gates de perfis de release, materializacao do runtime, bootstrap do backend, contrato do runtime e pacote `app-update` foram aprovados.
- Sintaxe dos JavaScript alterados e `git diff --check` foram aprovados.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.151`, `release_mode=app-update` e `publish=true`.

O workflow valida o pacote, gera `SHA256SUMS.txt` e publica os artefatos a partir de um draft completo. A copia instalada nao participa deste procedimento.
