# Renovação Bling compartilhada entre computadores

## Resultado preparado

A modalidade por provedor usa a Central existente somente para as conexões
Bling selecionadas. Mercado Livre, Mercado Turbo, dados operacionais e o
compartilhamento legado continuam com seus fluxos atuais. O modo anterior que
centraliza todas as integrações continua disponível.

Uma conexão central possui uma autoridade de renovação por aplicação e conta
Bling. Os outros usuários recebem uma concessão de acesso a essa mesma conexão.
Os tokens ficam no servidor; o desktop usa uma referência privada em memória.

## Comportamento

- A ação **Centralizar Bling desta loja** apresenta a prévia e pede confirmação
  de que os computadores foram atualizados e as sessões antigas estão fechadas.
- Lojas locais que usam a mesma conexão Bling entram juntas na prévia. As demais
  contas podem ser migradas em etapas posteriores.
- A preparação dessa modalidade somente consulta a identidade da conta. Não
  renova credenciais locais antes de a Central assumir o controle. Se o acesso
  estiver inválido, será necessária uma autorização explícita.
- A adoção valida a conta real, protege a repetição da operação e mantém o
  escopo de lojas/provedor no backup e na retomada após uma resposta incerta.
- O login ou a atualização explícita das lojas substitui somente os blocos
  Bling autorizados e neutraliza seus tokens locais, inclusive no backup imediato.
- Um snapshot antigo, callback OAuth atrasado ou restauração de backup não pode
  reativar a renovação local de uma conexão migrada.
- A perda da sessão Central deixa o Bling indisponível até renovar a sessão.
  As integrações locais continuam disponíveis enquanto sua sessão JK for válida.
- Uma sessão limitada ao Bling não pode alterar ou conceder acesso a outra
  integração central. Concessões e exclusões de lojas centrais mistas são
  bloqueadas nessa sessão para preservar o Mercado Livre.
- No fluxo local, alterar apenas a data da configuração não equivale a renovar
  o token. A consulta expirada ainda executa a renovação em vez de reutilizar
  a mesma credencial e invalidar a conta por um segundo erro de acesso.

## Compatibilidade

O bootstrap assinado acrescenta a modalidade `sync_mode: provider` e o escopo
fechado `providers: [bling]`. O contrato completo anterior permanece válido.
Clientes que não anunciam a capacidade `X-JK-Bling-Central-Protocol: 1` recebem
pedido de atualização ao tentar entrar com um usuário migrado nessa modalidade.

O campo de usuário `central_accounts_providers: [bling]` é distinto da ativação
completa `central_accounts_enabled`. Uma concessão Bling habilita somente o
provedor para o destinatário. Cada solicitação continua validando usuário,
máquina, empresa, loja, permissão e escopo no servidor.

O compartilhamento de arquivos pode continuar transportando Cadastro e demais
dados. Ele não é uma autoridade de renovação OAuth. Referências Bling centrais
não contêm credenciais e precisam de uma sessão Central autorizada para funcionar.

## Ativação após aprovação e publicação

1. Publicar a revisão correspondente do gateway e do desktop, mantendo a Central
   configurada com armazenamento e callback de produção válidos.
2. Atualizar todos os computadores envolvidos e fechar processos JK antigos.
   O bloqueio de novo login não encerra um processo antigo já em execução.
3. O responsável pela conexão migra uma conta piloto, faz novo login e valida
   a consulta Bling nos computadores participantes.
4. Para outros usuários, conceder acesso à conexão piloto pela Central. Eles
   entram novamente e usam essa mesma conexão; não repetem a migração de uma
   cópia antiga dos tokens.
5. Validar uma renovação e as operações usuais antes de ampliar às demais contas.

Não restaurar tokens locais depois de uma ativação central confirmada. Uma
recuperação de autorização deve acontecer na Central, pois um refresh token
anterior pode já ter sido consumido. Uma revogação real ou renovação cujo
resultado fique incerto ainda pode exigir reconexão explícita.

## Verificação

Os testes usam contas, credenciais, transportes e diretórios temporários
sintéticos. Cobrem concorrência entre computadores, isolamento por provedor e
empresa, assinatura alterada, sessão expirada, resultado incerto, migração
incremental, aliases de conta, callbacks tardios, corrupção e recuperação de
backup, preservação de ML/Turbo e o fluxo da interface no navegador.

A execução em contas reais, a disponibilidade da revisão em produção e a
homologação nas máquinas participantes fazem parte da ativação posterior.
