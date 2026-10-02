# Validação da versão 1.0.160

Data: 2 de outubro de 2026.

## Integração

Alteração de fornecedor implementada e validada em worktree dedicado, incorporada ao commit publicado `bfce78e2d7899bb650f6d1089f437750717a7e48` da versão 1.0.159. A auditoria dos trabalhos pendentes confirmou que as demais correções aprovadas já estão integradas; a implementação de responsividade ainda em andamento permanece isolada.

A próxima versão foi calculada a partir das tags remotas, consultadas novamente antes da alteração dos arquivos. Package files, lockfiles, testes de versão e manifesto do Context Hub estão alinhados em 1.0.160. O seletor compartilhado de fornecedores foi acrescentado aos arquivos obrigatórios e à verificação de paridade do instalador.

## Verificações

- 107 testes Python de fornecedores, listas, importação, Invoice e relatórios, além de 6 testes de distribuição e alinhamento de versão, aprovados.
- 15 verificações de interface aprovadas, incluindo navegação real em navegador: geração POST/GET, importação Excel nos dois módulos, cadastro vazio, erro, cancelamento, vinculação de listas antigas, troca de fornecedor e nomes longos nos cartões.
- Casos de fornecedor ausente, ID de outro cliente, nome adulterado e remoção do vínculo rejeitados. Criação indireta por relatórios exige fornecedor antes de gravar fila ou lista e preserva a aprovação existente.
- As 12 falhas de `test_cadastro_photo_store_consumers.py` foram reproduzidas no commit de origem `43f9f590d758a170276db93726007d8111130b64`, sem a alteração. Esse arquivo foi excluído da nova execução proporcional; a publicação não declara aprovação da suíte completa.
- Dependências, 24 espelhos HTML, manifesto do Context Hub, contrato de runtime, perfis de release, materialização, bootstrap, extração WiX e staging aprovados.
- 11 arquivos Python alterados compilados; `git diff --check` aprovado.
- Build local pequeno realizado com `npm.cmd --prefix electron_app run dist:update`, com verificação do pacote concluída. Paridade de 17 arquivos de produção alterados confirmada no pacote. Nenhum instalador foi executado.

## Publicação

Workflow `desktop-release.yml` configurado com `release_tag=v1.0.160`, `release_mode=app-update` e `publish=true`. O workflow prepara os pacotes e publica Setup, Update, seus blockmaps, `latest.yml` apontando para Update e `SHA256SUMS.txt` após os gates.

A cópia instalada não participa da integração ou dos testes. Versão e hashes do package e do manifesto da instalação são conferidos antes e depois da publicação.
