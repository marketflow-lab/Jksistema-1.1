# Validacao da versao 1.0.155

Data: 23 de setembro de 2026.

## Escopo

A aba Importador/loja recebeu formulario e API por cliente e loja. Os registros operacionais permanecem fora do codigo versionado.

## Verificacoes

- Testes direcionados da interface, do isolamento dos registros e dos contratos de distribuicao.
- Contrato de dependencias e sintaxe JavaScript/Python.
- Validacao do pacote de atualizacao e `git diff --check` antes da publicacao.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.155`, `release_mode=app-update` e `publish=true`.
