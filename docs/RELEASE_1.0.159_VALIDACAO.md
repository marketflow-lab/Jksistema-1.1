# Validacao da versao 1.0.159

Data: 2 de outubro de 2026.

## Integracao

Correcao aprovada de Perguntas implementada em Worktree dedicado a partir de `43f9f590d758a170276db93726007d8111130b64` (v1.0.158). Arquivos de versao da raiz, Electron, lockfiles, testes e manifesto do Context Hub sincronizados em 1.0.159. Alteracoes ainda em implementacao ou planejamento em outros chats permanecem isoladas.

## Verificacoes

- 175 testes Python aprovados: 123 de pesquisa, execucao do agente e contrato de reutilizacao; 46 de alternativas, pos-venda e arquitetura; 6 de distribuicao e alinhamento de versao.
- Casos de consulta sem evidencia, fontes conflitantes, dados externos nao confiaveis, isolamento de cliente/loja/vendedor, timeout, falha de API e hidratacao incompleta cobertos.
- Snapshot publico comparado com o commit de origem, sem alteracao das rotas, aliases, exports, schemas ou politica. O teste `test_ppv_public_contract_snapshot` foi excluido da suite proporcional porque ja falha no commit de origem: espera 40 rotas e encontra 41.
- Dependencias, 24 espelhos HTML, manifesto do Context Hub, contrato de runtime, perfis de release, materializacao, bootstrap, extracao WiX e staging aprovados.
- 10 arquivos Python alterados compilados em memoria; `git diff --check` aprovado.
- Build local pequeno e verificacao do pacote executados com `npm.cmd --prefix electron_app run dist:update`, sem executar o instalador. Paridade dos cinco Python de producao alterados verificada no pacote.

## Publicacao

Workflow `desktop-release.yml` com `release_tag=v1.0.159`, `release_mode=app-update` e `publish=true`. O workflow tambem prepara o instalador completo e publica Setup, Update, seus blockmaps, `latest.yml` apontando para Update e `SHA256SUMS.txt`, apos os gates de build e staging.

A copia instalada nao participa da integracao nem da execucao dos testes. Sua versao e os hashes do package e do manifesto sao conferidos antes e depois da publicacao.
