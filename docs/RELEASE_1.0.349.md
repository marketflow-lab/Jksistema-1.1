# JK Sistema 1.0.349

Atualização pelo canal `app-update` com a correção da renovação das contas Bling
usadas em mais de um computador ou usuário.

- Permite centralizar somente o Bling, começando por uma conta piloto e mantendo
  os fluxos de Mercado Livre, Mercado Turbo e compartilhamento de arquivos.
- A Central mantém uma única conexão e coordena a renovação entre computadores.
  Outros usuários recebem acesso à mesma conexão.
- Impede que backups, snapshots antigos ou callbacks atrasados reativem tokens
  locais depois da migração.
- Protege a retomada da migração após falhas e identifica lojas que usam a mesma
  autorização Bling, evitando deixar cópias locais renovando em paralelo.
- Diferencia indisponibilidade da sessão Central de necessidade de autorizar a
  conta Bling e corrige a reutilização de token expirado por mudança apenas na
  data da configuração.

A publicação inclui a revisão correspondente do serviço Central. A ativação é
feita pelo responsável pela conexão, após atualizar os computadores e encerrar
processos JK antigos. Os demais usuários recebem acesso à mesma conexão; não
devem migrar novamente uma cópia dos tokens. Contas já invalidadas podem exigir
uma autorização explícita.

O `app-update` reutiliza o runtime compatível existente. O Setup completo
acompanha a mesma release.
