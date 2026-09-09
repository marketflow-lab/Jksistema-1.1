# Listagem de lojas independente das transacoes de catalogo

A tela de Perguntas consulta uma projecao publica local, sem aguardar os locks de
lojas, catalogo ou fotos. A fonte operacional continua sendo a configuracao
canonica; envio, OAuth e alteracoes nunca usam a projecao como autorizacao.

## Coordenacao e publicacao

`store_coordination` coordena pelo diretorio fisico validado, com reentrada e
exclusao entre processos. Ordem: legado global (quando aplicavel), tenants em
ordem canonica, catalogo, custos, fotos e SQLite. As aquisicoes compartilham um
prazo de dez segundos. Os mutexes existentes de arquivos/SQLite sao preservados.

`store_listing_service.lojas_config_lock` delimita o owner externo. Gravacoes
internas apenas registram preimages; a publicacao ocorre depois das validacoes e
das etapas reversiveis. `store_public_snapshot` possui schema fechado e grava
`lojas_public_snapshot.json` por substituicao atomica, sem credenciais. Na Central,
as lojas publicas vem da sessao autenticada, sem cache compartilhado entre usuarios.

`store_snapshot_transactions` mantem a barreira duravel de publicacao:

- `prepared/rollback`: owners com reversao completa de lojas/tombstones recuperam
  preimages antes de validar e publicar (exclusao, desconexao e merges de lojas).
- `prepared/validate`: fluxos que podem confirmar CSV/SQLite independentemente
  preservam esses commits e executam a recuperacao/validacao canonica existente.
  Nao se reverte parcialmente o cadastro por causa de uma falha da projecao.
- `committed`: a operacao de negocio terminou; a publicacao/limpeza pode ser
  repetida sem repetir a mutacao. Preimages dispensaveis nao bloqueiam esse estado.

Um journal preparado invalido impede reconstruir a projecao. A ultima geracao
valida continua disponivel. A projecao e o diretorio privado `_stores_publication`
sao permanentemente excluidos da importacao e exportacao do Shared Sync.

## Contrato da tela

GET `/api/mercadolivre/perguntas/lojas` preserva `success` e `lojas` e acrescenta
`snapshot: {generation, published_at, status}`. Estados: `ready` e `updating`.
Sem geracao valida: HTTP 503, `Retry-After: 2`, e codigo
`stores_snapshot_initializing` ou `stores_snapshot_unavailable` em `detail`.
A recuperacao ocorre fora da requisicao, com exclusao por tenant entre processos.

O loader preserva cartoes e selecao na mesma sessao, ignora respostas antigas,
limpa estado no logout/troca de sessao e limita tentativas a 30 segundos, com
intervalos de 2, 4 e 8 segundos. Somente uma resposta `ready` valida e vazia
confirma ausencia de lojas. Falhas deixam o botao de nova tentativa disponivel.

## Validacao

As suites direcionadas exercitam escritor real bloqueado por catalogo e leitores
de dois tenants abaixo de 500 ms no ambiente controlado; concorrencia Windows
entre processos; exclusao e Shared Sync falhando apos commits internos; reinicio
abrupto entre gravacoes; reparacao da projecao sem repetir negocio; schema,
credenciais, sessao Central, retries e preservacao da selecao no navegador.

Regressoes executadas incluem Integrações/ciclo de vida, Cadastro, Estoque,
Central, Shared Sync, fotos, Context Hub, Vendas e contratos dos endpoints.
Os contratos versionados de rotas, assinaturas e schemas anteriores permanecem
iguais; a extensao de resposta possui assercoes funcionais adicionais.

A concorrencia entre processos foi executada no Windows. Casos que exigem criar
symlink/junction foram ignorados quando o sistema negou esse privilegio; os ramos
de redirecionamento simulado foram validados. O caminho POSIX nao foi executado
nativamente nesta maquina. Nenhuma medicao usa dados operacionais reais.

Alteracao preparada para a proxima versao, sem modificar numero de versao,
produzir instalador, publicar ou atualizar a copia instalada.
