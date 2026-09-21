# Validacao da versao 1.0.153

Data: 21 de setembro de 2026.

## Base e escopo

Release preparada sobre a `v1.0.152` publicada, incorporando as alteracoes aprovadas que removem duplicacoes e metadados internos do contexto enviado ao agente de respostas do Mercado Livre.

O perfil permanece `app-update`. Dependencias, runtime Python e contrato do instalador nao mudaram. Runtime preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Verificacoes locais

- 140 testes proporcionais do agente unificado, pre-venda, pos-venda, transporte e roteamento foram aprovados; duas falhas anteriores e reproduzidas na base permaneceram fora desta alteracao.
- Locks de 4 dependencias Node, 127 pacotes Python de runtime e 7 pacotes Python de teste foram conferidos.
- Os 24 espelhos HTML estaticos foram conferidos sem alteracoes.
- Gates de perfis de release, materializacao do runtime, bootstrap do backend e contrato do runtime foram aprovados.
- Testes de projecao confirmaram a remocao dos metadados internos e a preservacao literal dos documentos e orientacoes do Obsidian.
- Compilacao dos Python alterados e `git diff --check` foram aprovados.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.153`, `release_mode=app-update` e `publish=true`.

O workflow valida o pacote, gera `SHA256SUMS.txt` e publica os artefatos a partir de um draft completo. A copia instalada nao participa deste procedimento.
