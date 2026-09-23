# Validacao da versao 1.0.157

Data: 23 de setembro de 2026.

## Escopo

Bloco Buyer da Commercial Invoice preenchido a partir do cadastro Importador/loja da identidade exata da loja.

## Verificacoes

- Testes da Commercial Invoice, incluindo os 11 campos, isolamento por loja e ausencia de cadastro.
- Compilacao dos modulos Python alterados, contratos de dependencias e pacote de atualizacao.
- `git diff --check` antes da publicacao.

## Publicacao

Workflow `desktop-release.yml` com `release_tag=v1.0.157`, `release_mode=app-update` e `publish=true`.
