# Validação da versão 1.0.310

## Escopo

Publicação autorizada como `app-update`, com `publish=true`, integrando as correções aprovadas de Etiquetas e o commit pendente de carregamento dos dados e margens do SKU. A base publicada anterior é `v1.0.309`, no commit `d3bbbc82cf97413371849ef86744e7eeaf6e2f47`.

A base da release incorpora os commits `2f9cdb15` (Importações), `a82d3d7e` (editor de Etiquetas) e `47828f56` (configuração atual e tratamento da prévia). Os arquivos de versão e os dois testes de alinhamento foram atualizados para `1.0.310`. O aplicativo instalado não participa do handoff de desenvolvimento.

## Verificações concluídas

- 76 testes Python aprovados nos alvos de distribuição do runtime, alinhamento de versões, transporte do Mercado Livre, isolamento de workers, margens dos concorrentes e responsividade do servidor.
- Compilação dos 17 arquivos Python alterados desde a versão anterior e `git diff --check` aprovados.
- As cinco regressões Node de Importações passaram: fontes dos concorrentes, margens, carregamento do SKU, regressão do SKU e preservação dos dados salvos.
- Os arquivos fonte e testes de Etiquetas foram conferidos por SHA-256 e são idênticos aos já validados. Foram reaproveitados os 52 testes e 34 subtestes, os 14 casos afetados da correção final e o teste Playwright completo do editor, incluindo API lenta, erros 405, troca de sessão, sobreposição, recorte DANFE e celular.
- Locks das dependências, espelhos HTML, contrato do runtime e pacote de atualização na fonte aprovados.
- Perfis de release, materialização do runtime, bootstrap do backend, extração WiX e staging dos artefatos aprovados. A materialização foi executada fora do sandbox após uma restrição local de renomeação atômica; os testes usam fixtures temporárias.

Runtime preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`. Os arquivos `installer-runtime.lock.json`, `runtime-versions.json` e os locks Python permanecem inalterados.

## Publicação

Executar `desktop-release.yml` com `release_tag=v1.0.310`, `release_mode=app-update` e `publish=true`, no commit da release. O workflow valida e empacota os artefatos, publica a partir de draft e aponta `latest.yml` para o Update leve. O resultado da execução e os assets publicados são conferidos antes de concluir a tarefa.
