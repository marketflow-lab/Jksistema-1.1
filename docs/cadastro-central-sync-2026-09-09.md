# Cadastro manual e Central de Contas

Base: release estável publicada v1.0.139, commit ec553cc4563036e600987163eac6a38e85988b7d, confirmada no GitHub antes da criação do worktree.

## Comportamento entregue

- Cadastro reconhece eventos de restauração e exclusões posteriores, usando a mesma regra para produtos, custos e configuração de fotos.
- Importação valida referências antes de gravar e mantém uma transação recuperável para o pacote completo. Uma falha posterior reverte produtos, custos, metadados, fotos e variantes já aplicados; o recebimento só é confirmado após sucesso.
- O modo entre máquinas é manual. Configurações antigas não reativam a automação. O fluxo continua por prévia, Enviar agora e Importar agora.
- A tela do Cadastro atualiza após importação em outra aba ou iframe da mesma sessão, preservando edições em andamento. O aviso de importação não equivale à validação de OAuth.
- Atualização da Central materializa somente identidades e metadados autorizados no índice local, com o bloqueio entre processos usado pelo cadastro de lojas. Identificadores e informações locais adicionais são preservados.
- Cadastro continua usando Shared Sync. Pacotes legados de credenciais não podem ser exportados ou aplicados após migração. Lojas revogadas e grupos de fotos com destinos não autorizados bloqueiam o pacote.
- Migração verifica disponibilidade, mantém a mesma operação e consulta o estado remoto antes de repetir. Uma resposta incerta não permite consumir o mesmo refresh token novamente. A interface distingue conclusão remota de finalização local.
- A Central distingue credencial rejeitada, limitação temporária e resultado incerto. Renovação concorrente usa reserva transacional; repetição de adoção não sobrescreve a autoridade existente nem restaura lojas excluídas.

## Compatibilidade

Rotas de prévia, push, pull e migração e schemas de requisição foram preservados. A configuração de máquinas passa a mode_version 3, com auto_pull e auto_push desativados. O status de migração recebe campos aditivos: preview_fingerprint, remote_error e needs_local_finalize. Os hashes de contratos públicos existentes são verificados pelos testes.

## Validação

As regressões abrangem restauração e exclusão posterior, SKU com zero inicial, mesmo nome/SKU em lojas diferentes, ida e volta de produtos/custos/fotos, reimportação idempotente, falha tardia com rollback de grupos e variantes, ausência de recibo em erro, repetição do mesmo snapshot, índice Central inicialmente ausente, revogação de acesso, credenciais legadas, concorrência OAuth, retomada de migração e notificações entre abas e frames.

Resultados consolidados: **1.140 casos Python distintos aprovados**, com 9 verificações de symlink/junction ignoradas por falta de privilégio do Windows. A coleta final dos 20 arquivos de testes confirma 1.149 casos ao todo.

Rodadas: Shared Sync amplo 521 aprovados / 3 ignorados; regressões de CRUD, custos, compatibilidade e fotos 520 aprovados / 6 ignorados; rodada integrada final de Cadastro, Shared Sync e Central 395 aprovados / 3 ignorados (há sobreposição entre rodadas). Também passaram 12 verificações JavaScript, incluindo os dois cenários no navegador Edge; 21 arquivos Python compilados, 8 JavaScript com sintaxe verificada, 4 pares de arquivos espelhados idênticos e git diff --check.

## Implantação autorizada posteriormente

O trabalho entrega código e testes em worktree. Não publica release, não gera instalador, não implanta Cloud Run e não altera a instalação ou dados operacionais.

A implantação deve alinhar as duas máquinas à versão que contém os dois commits, manter o modo manual e testar o Cadastro antes de migrar as conexões. A migração real depende da disponibilidade do gateway e da autorização da sessão; apresentar prévia, recuperar somente as conexões necessárias e entrar novamente nas duas máquinas. Confirmar os dados visíveis por loja e o acesso Mercado Livre/Bling. A validação realizada aqui usa dados sintéticos e provedores simulados; não comprova validade atual dos tokens de produção.

Se a ativação central tiver sido confirmada, uma reversão não deve restaurar tokens locais possivelmente consumidos. Deve conservar a autoridade central e os dados do Cadastro, usando a recuperação explícita da conexão quando necessária.
