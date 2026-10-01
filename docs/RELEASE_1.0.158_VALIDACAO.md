# Validacao da versao 1.0.158

Data: 1 de outubro de 2026.

## Integracao

Correcao de promocoes `5cc5783` integrada com a release oficial `v1.0.157`, sem conflitos. Manifestos da raiz, Electron, lockfiles e Context Hub sincronizados em `1.0.158`. Contrato de runtime e dependencias preservados.

## Verificacoes

- Casos de base divergente: desconto ML de R$ 2,40 nos tres modos de analise.
- Beneficio parcial: R$ 3,58 estimado, tarifa final de R$ 15,25 e margem sem segundo abatimento.
- 427 testes e 87 subtestes aprovados: suite proporcional de promocoes, Cadastro, Commercial Invoice, distribuicao, provisionamento offline e alinhamento de versao. Dados temporarios isolados, sem consultas aos anuncios reais.
- 14 verificacoes JavaScript de promocoes, Cadastro e Importacoes aprovadas.
- Dependencias, 24 espelhos HTML, contrato de runtime, perfis de release, materializacao, bootstrap e staging aprovados.
- Fixture de foto/layout da Commercial Invoice isolada da resolucao real do cadastro de lojas, eliminando dependencia da ordem de testes.
- 18 Python compilados em area temporaria e `git diff --check` aprovado antes do commit.

## Publicacao

Workflow `desktop-release.yml` com `release_tag=v1.0.158`, `release_mode=runtime-update` e `publish=true`.

O workflow exige a verificacao do pacote e o smoke profundo `--deep-runtime` antes do staging/publicacao. O smoke usa instalacao temporaria, wheels locais, `pip check`, imports do backend e verificacao de idempotencia. A publicacao precisa concluir esses gates e entregar somente Setup, blockmap, `latest.yml` e `SHA256SUMS.txt`.

O aplicativo instalado permanece fora da execucao.
