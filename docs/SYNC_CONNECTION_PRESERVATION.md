# Sincronizacao entre maquinas com preservacao de conexoes

Base: 1.0.137, commit 484e509. Implementacao autorizada em 08/09/2026,
com a restricao adicional de que sincronizar nao pode desconectar lojas.

## Comportamento

- No recebimento entre maquinas, todo bloco de conexao local preexistente e
  preservado, incluindo tokens, identificadores, estado de conexao e consentimento
  OAuth em andamento. Um pacote remoto nao desconecta nem reconecta uma loja local.
- Configuracoes divergentes geram uma contagem de pendencias; nao sao resolvidas
  por timestamps nem pela troca automatica de tokens. Novas lojas ainda entram
  pelo merge validado de identidades. Exclusoes remotas continuam sem propagacao.
- O arquivo legado de integracoes nao pode reintroduzir credenciais antigas sobre
  as conexoes canonicas. A pos-condicao e verificada apos a gravacao, sob o mesmo
  lock; a falha reverte a transacao. Metadados internos de versao podem evoluir.
- A sincronizacao nao chama renovacao OAuth, desconexao, migracao de contas ou
  encerramento de sessao. O funcionamento central existente continua separado.

## Dados e confirmacao

- Recebimento automatico retorna falha ou resultado parcial quando houver erro.
  O erro estruturado pode ser auditado por codigo, sem credenciais ou payloads.
- Cadastro e Integracoes permitem receber enquanto a tela e consultada.
  Operacoes locais ativas suspendem o recebimento na tela de Cadastro.
- O cadastro recarrega quando recebe o evento de importacao. A lista vazia limpa
  a paginacao, e paginas fora do intervalo sao ajustadas.
- Um hash remoto igual nao basta para ignorar a importacao: os modulos Cadastro
  e Lojas precisam de uma assinatura local de nomes relativos, tamanhos e mtimes.
  Arquivos removidos ou substituidos provocam nova conferencia do pacote.
- A maquina confirma a aplicacao por snapshot e pela identidade de maquina da
  sessao autenticada. O remetente consulta a confirmacao pelo endpoint de status
  existente. O protocolo de recibos e 1, sem mudanca de rotas ou requests.
- Recibos ficam na subcolecao `receipts` do pacote da mesma conta e usuario.
  Cada maquina possui um documento identificado por hash. O recibo contem somente
  versao, identidade/hash do snapshot, data de aplicacao e contagem de conflitos.
  Ele confirma dados aplicados, jamais validade OAuth ou conexao ao provedor.
- Falha ao publicar o recibo deixa a confirmacao pendente e permite tentativa
  idempotente posterior, sem desfazer dados aplicados ou alterar conexoes.
  Escrita/leitura de recibos tem timeout de cinco segundos, sem retry do SDK.
- Falhas automaticas aparecem na tela. Falhas transitorias usam espera crescente
  com limite de tres tentativas; conflitos exigem acao explicita para tentar de novo.

## Limites e ativacao

Uma credencial que o provedor ja recusou nao se torna valida por ser copiada.
Esta mudanca impede que o Shared Sync derrube conexoes existentes; nao promete
impedir revogacao ou expiracao pelo Mercado Livre/Bling. Conexoes divergentes ou
invalidas permanecem como pendencia, sem apagar a configuracao local.

A instrucao de preservar sessoes impede executar a migracao atual da Central de
Contas durante a sincronizacao, pois ela remove segredos locais e encerra a sessao.
Nenhuma migracao ou reautorizacao de conta real foi executada nesta entrega.

Maquinas anteriores ao protocolo de recibos nao emitem confirmacao. Instalar apenas
na origem nao comprova recebimento em destinos antigos. A homologacao real exige
aplicar o codigo aprovado nos dois ambientes do projeto e validar as consultas.

## Validacao

- Suite ampliada: 628 testes Python aprovados; tres casos de junction/symlink
  ignorados por limitacao de permissao do Windows.
- Suite complementar: 216 aprovados, com dois skips de junction (ha sobreposicao
  com a suite ampliada; os totais nao devem ser somados).
- Navegador: recebimento no Cadastro, falha parcial visivel, bloqueio de repeticao
  automatica de conflito, pausa durante edicao, tentativa manual, atualizacao da
  tabela apos recebimento e limpeza da paginacao vazia.
- Preservacao de tokens e estado, rollback apos falha intermediaria, transferencia
  entre maquinas simuladas, isolamento de tenant/usuario/snapshot nos recibos,
  indisponibilidade da confirmacao e tentativa de usar identidade de outra maquina.
- Contratos de rotas/requests congelados em
  `tests/contracts/shared_sync_connection_safety_v1.json`.
- Falha preexistente: `cadastro_frontend_architecture.js` reprova o limite de
  400 linhas de `06-importacoes-catalogos.js`, que ja excede esse limite na base.
  Esse componente nao foi alterado por esta entrega.

Correcao preparada em worktree dedicado para a proxima versao, conforme a
orientacao mais recente do usuario. Homologacao nas duas maquinas reais permanece
pendente. Sem integracao ao checkout principal, mudanca de versao, push, publicacao,
instalador ou alteracao da copia instalada.
