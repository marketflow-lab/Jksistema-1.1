# Validação da versão 1.0.161

Data: 2 de outubro de 2026.

## Integração e autorização

A correção de responsividade foi implementada e validada no commit `d1a855b02fe8cb7953069ec4c1f5bb42b157d5d0`, em Worktree dedicado, sobre a versão 1.0.160 (`3d0811dc2725d6065db083186b6230706b30fddd`). A publicação mantém as correções anteriores de pesquisa de anúncios e fornecedor obrigatório. O pedido explícito de publicação após a apresentação do diff autoriza integrar a correção aprovada, atualizar a versão, fazer commit/push e executar o workflow com `publish=true`.

A tag livre foi calculada pelas tags remotas e conferida antes da atualização dos arquivos. Os package files, lockfiles, manifesto do Context Hub, exemplo do workflow e asserções de versão estão sincronizados em 1.0.161. A branch de publicação é `codex/release-v1.0.161-responsividade`. Alterações locais da configuração do Codex e o artefato local já existente são preservados.

## Verificações da versão final

- 121 testes Python passaram em 204,88 segundos: servidor HTTP real, cancelamento, downloads, transações de listas, importação, aprovação, trânsito, sugestões, fornecedores, relatórios, watcher, cache, arquitetura e contratos de distribuição.
- O cenário Uvicorn mantém dois workers pesados bloqueados por 50 segundos e uma terceira operação aguardando. `/health`, Dashboard, Vendas, PPV e Importações respondem em menos de um segundo. Casos adicionais verificam o mutex real de fornecedores e gravações após desconexão.
- 39 arquivos Python alterados desde a versão anterior compilaram em memória. `git diff --check` passou.
- Dependências, manifesto do Context Hub, contrato de runtime e pacote update fonte passaram. Os quatro contratos e pins de runtime/dependências mantêm os mesmos blobs da versão 1.0.160.
- Perfis de release, materialização, bootstrap, extração WiX, staging e saneamento do pacote passaram. Os 24 espelhos HTML e seu contrato de verificação somente leitura passaram.
- Build local pequeno concluído com `npm.cmd --prefix electron_app run dist:update`, incluindo `verify:update:built`. O manifesto inclui 1.067 arquivos gerenciados. Os 24 arquivos de produção alterados estão presentes e iguais às fontes por SHA-256, sem divergências.
- Update local: 92.155.913 bytes; SHA-256 `a2486879e65f7cd9151a333ce4acb187ab9bc12a0772ade103081dbeefc142ad`. A versão e o SHA-512 de `latest.yml` foram conferidos. O workflow realiza outra construção; os hashes dos artefatos publicados serão verificados independentemente.

A primeira tentativa da suíte final encontrou 72 erros de preparação porque faltava a pasta pai do diretório temporário isolado. A pasta foi criada e a execução completa posterior passou sem falhas. Não houve alteração de código para resolver esses erros.

A implementação também passou, antes do incremento de versão, por 359 testes de IA e WhatsApp e pelas rodadas de integridade e Context Hub registradas no relatório de diagnóstico. A comparação de 183 casos antigos reproduziu 138 falhas tanto na base original quanto na implementação, com resultados idênticos. A publicação declara os gates e testes proporcionais acima, sem declarar aprovação da suíte completa.

## Desempenho

As medições sintéticas e sua metodologia estão em `docs/diagnosticos/responsividade-jk-20261002.md` e no JSON de métricas agregado. A latência máxima das páginas caiu de 2.994,41 ms para 18,77 ms com carga equivalente. Na seleção de WhatsApp com 2.000 aprovações históricas e quatro pendentes, conexões SQLite caíram de 6.003 para 2, incluindo revalidação fresca; essa carga não contém tokens ativos. Seis construções de inventário passaram de 14,421371 s sem reuso para 7,474481 s com cache aquecida. Dados operacionais e o aplicativo instalado não participam das medições.

## Publicação e instalação

Workflow `desktop-release.yml` configurado com `release_tag=v1.0.161`, `release_mode=app-update` e `publish=true`, após o commit e push da branch. O workflow prepara Setup e Update, publica ambos com seus blockmaps e `SHA256SUMS.txt`, e direciona `latest.yml` ao Update pequeno. A publicação passa pelos gates de build e staging antes de sair de draft.

Nenhum instalador é executado nesta tarefa. As duas cópias locais identificadas permanecem na versão 1.0.155; hashes do package e do manifesto da instalação são conferidos antes e depois. As dependências e a configuração temporárias do build foram removidas sem alterar suas fontes.
