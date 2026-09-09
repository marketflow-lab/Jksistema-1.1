# Firebase após o login

O login remoto negocia `X-JK-Firebase-Protocol: 1`. O servidor assina a identidade,
as permissões e a extensão `firebase_access` na mesma resposta. O aplicativo
mantém ID token e refresh token apenas na memória do backend local. A interface
recebe somente o estado da conexão. Nenhuma chave privada administrativa é
distribuída ou incluída no pacote.

Cada sessão recebe uma autorização Firestore com validade máxima de oito horas,
vinculada ao usuário, empresa e máquina autenticados. O token é renovado durante
esse período. As regras conferem a autorização e a política atual em cada acesso.
Após reiniciar o backend ou expirar a sessão, é necessário fazer login novamente.
Uma sessão restrita não usa a credencial administrativa local como alternativa.

## Dados autorizados

O acesso contempla o perfil público e a sincronização entre as máquinas do próprio
usuário, nos módulos permitidos. Os identificadores existentes de snapshots v2 e
chaves de criptografia são preservados. O conteúdo continua criptografado pelo
protocolo de sincronização existente. A leitura dos documentos administrativos de
usuários e das coleções centrais permanece bloqueada para esses tokens.

Receber a credencial e as permissões após o login não aplica automaticamente um
snapshot ao banco local: Enviar agora e Importar agora continuam sendo ações
manuais. O protocolo novo não concede acesso a compartilhamentos de outros
usuários nem aos canais administrativos de presença em tempo real.

Snapshots antigos sem criptografia (v1) não recebem permissão no protocolo novo.
Uma migração desses dados exige tratamento separado no servidor.

## Ativação na próxima versão

Publicar primeiro as regras de `firestore.rules`, depois o serviço
`cloud/auth_gateway` e, por último, distribuir o cliente atualizado. O gateway usa
a configuração Firebase existente, inclusive a chave Web pública e a identidade
de serviço. O usuário final não seleciona arquivo JSON.

Um gateway anterior ignora a negociação e mantém o login existente. Nesse caso,
o aplicativo informa que a conexão automática ainda precisa ser ativada no
servidor. A configuração administrativa legada continua disponível para sessões
legadas autorizadas. A rota dessa configuração foi registrada no aplicativo.

Esta alteração de código não publica regras, gateway, versão ou instalador.

## Validação

Os testes cobrem assinatura da extensão, troca de empresa/máquina, renovação,
expiração, isolamento de requisições síncronas e assíncronas, ausência de segredos
nas respostas e status. A suite de regras usa exclusivamente um projeto `demo-`
em emulador Firestore local. A execução dos testes não requer credenciais reais.

Os contratos públicos anteriores de rotas e schemas de Shared Sync permanecem
inalterados; os hashes de `shared_sync_connection_safety_v1.json` foram preservados.

Validação em 09/09/2026:

- Dependências fixadas do aplicativo: 58 testes de sessão/login e 35 testes de
  gateway/regras aprovados, incluindo 11 no emulador com upload, download,
  criptografia e publicação transacional reais.
- Regressões adicionais: 278 testes aprovados e um teste de symlink ignorado por
  falta de privilégio do Windows. Outro grupo teve 241 aprovados, dois ignorados
  pelo mesmo motivo e cinco falhas preexistentes de configuração do contexto de
  Integrações. As cinco falhas foram reproduzidas no checkout principal, que não
  contém esta alteração Firebase. O worktree foi criado a partir de `26b58e8`.
- Regressões JavaScript de Shared Sync e provisionamento aprovadas; espelhos HTML
  alinhados, compilação Python e verificação de espaços do Git aprovadas.
