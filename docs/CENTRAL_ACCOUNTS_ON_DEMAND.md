# Central de contas e primeiro acesso

Implementação autorizada em 05/09/2026. Base limpa: v1.0.133,
commit 4b323be8eab154e2b12f0057c90ef043ea40a7d5. A aprovação inicial cobriu implementação e testes no Worktree. Em seguida,
o usuário aprovou integração, publicação, implantação e atualização instalada.

## Comportamento aprovado

- O instalador contém somente o endereço confiável da central e configuração pública.
- O login autoriza a máquina e carrega lojas, permissões e configuração mínima.
- Credenciais administrativas do Firebase e tokens de Mercado Livre/Bling ficam no servidor.
- Produtos, vendas, estoque e arquivos operacionais são atualizados somente por comando do usuário.
- Consultar dados locais não deve gerar sincronização remota.
- Uma operação solicitada pode renovar a conexão na central quando necessário.
- Tenant, usuário, máquina, loja, conexão, seller e site mantêm identidades separadas.

## Contrato e integração

O contrato legado foi congelado em tests/contracts/central_accounts_v1_baseline.json.
A extensão central é opcional, versionada e coberta pela assinatura do login.
Uma sessão local nunca confere autoridade para outra empresa ou loja; a central
consulta a autorização atual antes de cada operação. Configuração pública não
contém segredos. Metadados em cache são separados das configurações OAuth legadas.

O trabalho inclui serviço de contas, autorização por loja, armazenamento protegido
de conexões, coordenação de renovação e cliente desktop compatível com as rotas
existentes. A implementação foi validada primeiro com dados sintéticos. A publicação
subsequente preserva os controles de ativação por usuário.

## Verificação necessária

Primeiro login sem arquivo de usuários ou service account; isolamento entre
usuários, tenants e lojas; máquina revogada; sessão expirada; assinatura alterada;
indisponibilidade da central sem fallback de credenciais; renovação concorrente;
timeout com resultado incerto; rejeição de URL externa; dados sensíveis ausentes
de respostas de bootstrap, caches públicos e logs; nenhuma sincronização automática.

## Ativação

Configurar a identidade de serviço, armazenamento de credenciais e callbacks
somente na implantação autorizada. Conectar as lojas na central e conceder acesso
explicitamente. A transição de contas legadas precisa impedir que instalações
antigas continuem renovando a mesma conexão; reautorizar a conexão central quando
necessário. Não copiar tokens de máquinas antigas silenciosamente.

## Configuração de implantação

1. Publicar uma versão do desktop que anuncie `X-JK-Central-Protocol: 1`.
2. Usar a identidade de serviço do Cloud Run com acesso restrito ao Firestore.
   Não criar ou distribuir JSON de service account.
3. Guardar uma chave aleatória de 32 bytes, em Base64, no Secret Manager e
   referenciar uma versão fixa como `JK_CENTRAL_VAULT_KEY`, somente no servidor.
   Conservar essa chave; sua troca exige migração dos documentos cifrados.
4. Definir `JK_CENTRAL_PUBLIC_ORIGIN` como a origem HTTPS do gateway, sem caminho,
   e `JK_CENTRAL_ENABLED=1`. Sem a flag, permanece o gateway de login anterior.
   Manter `JK_CENTRAL_REQUIRE_ENROLLMENT=1`: somente cadastros com o campo
   `central_accounts_enabled: true` recebem a modalidade central. Retirar esse
   campo ou definir `false` revoga também as sessões centrais já abertas.
   Publicar `/api/central/**` e `/api/auth/**` na mesma revisão do serviço.
5. Cadastrar nas aplicações Mercado Livre/Bling o callback exato
   `<origem>/api/central/v1/oauth/callback`. Conectar as contas em Lojas e APIs.
   Bling deve permitir a consulta de Empresas / dados básicos para identificar
   a conta pelo campo `data.id`; CNPJ não é usado como identidade.
6. Manter as coleções `jk_central_v1_*` inacessíveis aos SDKs dos clientes.
   O servidor usa `stores`, `connections`, `oauth` e `operations` com esse prefixo.
   Habilitar TTL no campo timestamp `delete_after` de `oauth` e `operations`.
7. Homologar a revisão com contas de teste e duas máquinas antes da liberação:
   login, concessão, revogação, catálogo, vendas, estoque e reconexão.
   A publicação foi autorizada. A conexão real de cada provedor ainda exige
   concluir o consentimento OAuth na conta correspondente.

## Comportamento implementado e limites

- A configuração mínima entra no hash assinado pelo Firebase. O token local
  identifica a modalidade central e tem um identificador único de sessão.
- A sessão privada dura oito horas e fica somente na memória do backend desktop.
  Reiniciar o backend requer novo login. Não há tokens das plataformas no desktop.
- Cada chamada remota valida usuário, validade, senha atual, máquina, loja,
  concessão e permissão do módulo. Revogar acesso independe de atualizar o cache.
- Abrir a tela de lojas consulta o cache do login. Atualizar lojas faz uma
  consulta de metadados; criar, conectar, desconectar e compartilhar são comandos
  explícitos. Os módulos mantêm seus comandos de consulta dos dados operacionais.
- As threads das operações manuais preservam o contexto de quem iniciou o trabalho.
- Sessões centrais não iniciam heartbeat Firebase, cópia de segredos, backup Drive
  ou Shared Sync automático. Os endpoints automáticos também são bloqueados.
- A conexão tem uma única autoridade de renovação por plataforma/aplicação/conta.
  Compartilhar concede acesso ao usuário e tenant de destino sem duplicar tokens.
- O POST de renovação fica fora da transação Firestore e não tem repetição
  automática. Timeout ou lease vencido exige reconexão, pois o refresh token pode
  ter sido consumido. Resultado incerto de uma mutação não provoca reenvio.
- Bootstrap: até 100 lojas. Resposta de plataforma: até 8 MiB. Hosts, recursos e
  headers são restritos; redirects e argumentos com credenciais são rejeitados.

O escopo da API central nesta entrega é contas Mercado Livre/Bling e o transporte
das consultas/operações desses provedores. Turbo, administração completa de usuários,
IA, Drive e transporte dos pacotes de arquivos do Shared Sync continuam nos serviços
legados; não foram migrados para essa API. É necessário considerar essa diferença
na homologação e na escolha dos usuários que receberão a modalidade central.

Contrato do Bling verificado em 05/09/2026: `/empresas/me/dados-basicos`, campo
`data.id`, na [referência oficial](https://developer.bling.com.br/referencia).
Nenhum teste usou contas reais. A documentação de ativação não executa implantação.

## Publicação autorizada em 05/09/2026

- Versão preparada: 1.0.134, integrada à melhoria de SKU existente no checkout.
- Chave gerada diretamente no Secret Manager, versão fixa 1, com acesso somente
  para a identidade de serviço do gateway. Nenhuma chave foi incluída no pacote.
- Cloud Run mantém mínimo zero e máximo duas instâncias. Hosting encaminha
  autenticação e central para a revisão homologada.
- Regras Firestore declaram somente as quatro coleções novas da central. A
  consulta anterior confirmou ausência de um ruleset Firestore publicado.
  A proposta inicial de regra global foi descartada na revisão de aprovação.
- TTL configurado para estados OAuth e registros temporários de operações.
  URLs de requisição do gateway são excluídas do armazenamento de logs para
  não persistir códigos OAuth que venham no callback.
- Prova integrada com usuários sintéticos: login legado, extensão assinada,
  duas identificações de máquina, concessão entre tenants, revogação e negação
  de acesso direto às quatro coleções usando Firebase ID token de cliente.
  Todos os documentos de teste foram removidos ao concluir.
- A ativação dos usuários reais exige indicar os logins que receberão o modo
  central e conectar as lojas. A instalação da atualização, por si só, não
  migra nem duplica tokens OAuth locais.
- Regressão ampliada: 1.080 testes Python aprovados, dois cenários de junction
  ignorados por limitação de permissão; 117 arquivos Node aprovados, incluindo
  as repetições direcionadas e o teste de atualização preservando dados locais.

## Validação da entrega

- 630 testes Python aprovados em 35 arquivos de regressão, cobrindo autenticação,
  central, integrações, catálogo, Mercado Livre, Bling, estoque e vendas.
- Cenários específicos: assinatura alterada, sessão/máquina expirada ou revogada,
  troca de tenant/senha, compartilhamento somente leitura, permissão de módulo,
  refresh concorrente/incerto, OAuth de uso único, headers/URLs/credenciais injetados,
  cache sem segredos e contexto do trabalho preservado entre threads.
- O teste Node de inicialização central comprova zero timers, chamadas de rede
  ou inicialização Firebase/Drive/Shared Sync automática e integra o runner padrão.
- 33 arquivos Python alterados/novos compilados. Scripts da tela e autenticação
  validados sintaticamente. Cópias raiz/static idênticas.
- Verificação dos locks aprovada. Manifesto ganha somente seis entradas para
  incluir os dois novos módulos locais nos três grupos de empacotamento.
- Contrato de Vendas mantém seu hash. A contagem global passa de 450/448 para
  453/451 rotas/pares de método e caminho, sem duplicatas.
- `git diff --check` aprovado. A validação foi local, com dados sintéticos.
  Não houve teste integrado com OAuth/Firestore reais nem validação de implantação.
