# Leitura das fichas do Obsidian

## Comportamento

A ficha de treinamento usa uma projeção local de exibição. Abrir um SKU não
enumera o cadastro, não lê o manifesto completo da loja e não percorre Markdown.
O Obsidian continua sendo a fonte editorial; aprovação e publicação para IA
continuam nos fluxos existentes.

`GET /api/mercadolivre/ia-treinamento/ficha?store_id=...&sku=...` autoriza a loja
pela sessão atual e consulta uma linha do índice pela identidade completa:
cliente, loja, seller, site, superfície e SKU. Zeros à esquerda são preservados.
Na Central, a autorização usa apenas as lojas da sessão. A projeção editorial
nunca concede acesso a uma loja.

O índice é `context_hub/training_read_index.sqlite`, privado de cada cliente,
com WAL, separado do banco transacional do Context Hub. Shared Sync e Drive já
excluem esse diretório. O índice é reconstruído localmente e não é fonte de
operações de negócio.

Ao sincronizar o Cadastro entre máquinas da mesma conta, o pacote também leva
uma projeção tipada do contexto editorial publicado. São transportadas as notas
efetivas da publicação ativa e as gerações ativas por loja e SKU, vinculadas ao
mesmo cliente e usuário. Arquivos do vault, configuração do Obsidian, SQLite,
WAL e SHM continuam fora do pacote. O destino valida loja, seller, site,
superfície, anúncio, variação e SKU antes de gravar, publica a projeção local e
reconstrói este índice. Contexto removido na origem só retira no destino o que
uma sincronização anterior registrou como pertencente à mesma origem.

## Atualização e consistência

A primeira consulta agenda a construção em segundo plano. Até haver uma geração
válida, retorna 503 estruturado com `Retry-After: 2`; inicialização e falha têm
códigos distintos. Havendo geração anterior, ela continua disponível durante
atualizações e falhas.

O atualizador verifica metadados a cada 2 segundos durante atividade, agrupa
alterações por 1 segundo e reconcilia integralmente a cada 60 segundos. Alterações
de notas e publicações confirmadas solicitam atualização do índice existente.
Um mutex por cliente coordena os processos; publicação SQLite é atômica e os
leitores não adquirem o bloqueio dos escritores.

`POST /api/mercadolivre/ia-treinamento/ficha/atualizar` recebe `store_id` e `sku`
e retorna 202. Recarregar a orientação continua disponível separadamente; uma
sincronização do Cadastro entre máquinas também agenda a reconstrução após
aplicar o contexto publicado recebido.
O andamento da sincronização é consultado por identidade autorizada, sem
enumerar produtos ou resolver novamente a configuração canônica de lojas.

Conteúdo e revisão editorial são publicados juntos. A revisão técnica das fontes
é distinta da revisão editorial: clientes novos enviam `expected_source_revision`
ao editar características, além do `expected_revision` já existente. Conflitos
retornam 409 e preservam a edição local. As validações canônicas continuam
obrigatórias para gravar.

## Tela

A tela mantém o último conteúdo válido em memória, separado por sessão e
identidade da loja. Mudança de sessão ou acesso negado limpa o cache e cancela
consultas. Uma ficha parcial não substitui orientações gerais ou outros SKUs.
Respostas antigas não podem desfazer um salvamento confirmado.

Falhas de atualização preservam a seleção e os rascunhos. A revisão-base de uma
edição não avança silenciosamente. Ausência de orientação só aparece depois de
uma leitura válida; não indexado, falha e ausência confirmada são distintos.
As tentativas usam intervalos de 2, 4 e 8 segundos, limitadas a 30 segundos.

## Verificação

As suítes `test_training_index_integration.py` e `test_training_index_worker.py`
cobrem a rota autenticada, isolamento, concorrência, publicação e atualização.
O benchmark controlado prepara 10 mil notas e 30 mil produtos; mede 50 consultas
da ficha com índice pronto e exige p95 inferior a 500 ms. Essa medição não inclui
a primeira construção do índice nem representa uma medição dos dados reais.

As regressões de treinamento e editor verificam compatibilidade das edições,
fontes técnicas, notas movidas, catálogo indisponível e controle de revisão.
Os testes de navegador verificam cache, tentativas, seleção e troca de sessão.
Logs de diagnóstico contêm apenas etapa, código fixo, contadores e duração.

Validação desta entrega no Windows:

- Benchmark final: p95 de 32,12 ms em 50 consultas autenticadas com índice pronto,
  10 mil notas e 30 mil produtos sintéticos. O agendamento é isolado nesse teste;
  outro cenário valida a consulta com o worker real e a fonte bloqueada.
- Regressões de Context Hub, catálogo e lojas: 369 aprovadas; dois casos de
  symlink pulados porque a conta Windows não tem esse privilégio.
- Lote final de worker, API, isolamento, encerramento abrupto, revisão e
  arquitetura: 74 aprovados.
- Navegador: ficha, treinamento completo e cartões de lojas aprovados; contratos
  de treinamento, salvamento rápido e persistência Codex aprovados.
- Compilação dos Python alterados, sintaxe JavaScript, igualdade dos HTMLs e
  `git diff --check` aprovados.

Os cenários de encerramento abrupto usam processos reais: um termina entre a
gravação das linhas e o commit do índice; outro deixa uma publicação do Context
Hub incompleta. Ambos preservam a leitura válida após recuperação. A política
existente de recuperação de locks de processos mortos continua exigindo cinco
minutos; o teste antecipa esse prazo alterando somente o timestamp da fixture.
