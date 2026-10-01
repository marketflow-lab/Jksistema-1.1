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

## Extracao do Python no runner

A extracao WiX usa processos ocultos com stdout/stderr redirecionados temporariamente, e repeticao limitada somente para o erro de pipe `0x800700e8`. Saidas parciais sao removidas antes de repetir; outros erros interrompem a preparacao. Pins de tamanho, assinatura e arvore Python continuam obrigatorios. O comportamento de repeticao/limpeza tem regressao PowerShell no workflow.

Extracao do bundle oficial tambem verificada localmente em processo sem console (`GetConsoleCP=0`), sem executar o instalador. Contexto do erro: [WiX issue 9267](https://github.com/wixtoolset/issues/issues/9267).

Helper final aprovado com extracao real e verificacao de tamanho/SHA1 dos cinco payloads MSI obrigatorios. Regressao PowerShell e gates de release reexecutados apos o ajuste.

## Publicacao

Workflow `desktop-release.yml` com `release_tag=v1.0.158`, `release_mode=runtime-update` e `publish=true`.

O workflow exige a verificacao do pacote e o smoke profundo `--deep-runtime` antes do staging/publicacao. O smoke usa instalacao temporaria, wheels locais, `pip check`, imports do backend e verificacao de idempotencia. A publicacao precisa concluir esses gates e entregar somente Setup, blockmap, `latest.yml` e `SHA256SUMS.txt`.

O aplicativo instalado permanece fora da execucao.
