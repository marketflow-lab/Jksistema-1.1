# Validacao da versao 1.0.156

Data: 23 de setembro de 2026.

## Escopo

Linha do tempo no balao de rastreamento publico da COSCO, alimentada pelo retorno da propria consulta.

## Verificacoes

- Parser COSCO e interface de rastreamento com os novos horarios.
- Contratos de dependencias, sintaxe JavaScript e validacao do pacote de atualizacao.
- `git diff --check` antes da publicacao.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.156`, `release_mode=app-update` e `publish=true`.
