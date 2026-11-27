# Expiração dos pacotes de sincronização

O serviço privado `jk-sync-expiration` no Cloud Run roda em São Paulo. O Cloud Scheduler chama `POST /expire` a cada cinco minutos com a identidade `jk-sync-expiry-invoke`. O serviço usa `jk-sync-expiry-runtime` e acesso de leitura e exclusão ao banco Firestore padrão.

O prazo é contado da confirmação registrada pelo Firestore. A rotina começa a remover os pacotes após 2h45 para deixar margem até 3h para reexecuções. Lojas e integrações fica fora da seleção. A rotina funciona com o aplicativo fechado. O recebimento de um pacote removido depende de um novo envio.

Use `GET /healthz` para verificar a disponibilidade do contêiner e os registros estruturados `shared_sync_expiration` para conferir as execuções. Para interromper o agendamento, pause `jk-sync-expiration` no Cloud Scheduler. A pausa impede execuções futuras e preserva os pacotes que ainda estiverem no Firestore.
