# Validação da versão 1.0.162

## Escopo

Publicação como atualização pequena (`app-update`). Inclui a correção aprovada de carregamento das listas de Importação e preserva a versão 1.0.161 publicada no commit `67d78c3467c793a51dc6419936683bd3152de194`, com responsividade e gravação atômica.

## Verificações locais

- 127 casos Python direcionados à integração de carregamento, concorrência, workers, fornecedores, trânsito, sugestões, margens e relatórios: aprovados. Inclui a concorrência entre recálculo em worker e troca de loja/quantidade, com contexto atualizado.
- 6 casos de contratos de distribuição e versões: aprovados. Total desta validação: 133 casos Python.
- 9 verificações da interface: carregamento, fornecedor obrigatório, concorrentes, aprovação, margens automáticas e de concorrentes, Commercial Invoice, SKU e layout dos documentos.
- Compilação dos 15 arquivos Python alterados e `git diff --check`: aprovados.
- Dependências, manifesto do Context Hub e 24 espelhos HTML: aprovados.
- Bootstrap, perfis de release, materialização de runtime, staging Electron, extração WiX e staging dos artefatos: aprovados.
- `npm --prefix electron_app run dist:update`: concluído com `verify:update:built` aprovado.
- Os 26 arquivos alterados de backend e interface correspondem por SHA-256 às fontes no pacote local.

## Pacote local

- Versão: `1.0.162`.
- Artefato: `JK-Sistema-Cliente-Update-1.0.162.exe`.
- Tamanho: 92.158.614 bytes.
- SHA-256: `0996e2b70f1f650166447953874843da1b39ab3a2aed583b2c60eb88fabd32ea`.
- Contrato de runtime preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

O hash acima identifica a compilação local. O workflow recompila o artefato publicado e gera seu próprio manifesto de hashes.

## Limites da validação

A suíte completa não é apresentada como aprovada. Falhas anteriores de ambiente e fixtures estão documentadas em `docs/diagnosticos/responsividade-jk-20261002.md`; as verificações proporcionais às alterações foram executadas e passaram.

A publicação utiliza `release_tag=v1.0.162`, `release_mode=app-update` e `publish=true`. O aplicativo instalado não é atualizado.
