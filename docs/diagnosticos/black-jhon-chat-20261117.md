# Black Jhon: falha ao iniciar uma conversa

Data: 17/11/2026. Base: `85195cd0`.

## Causas comprovadas

- A descoberta de `AGENTS.md` acionava um auxiliar do sandbox Windows que rejeita o perfil com a raiz negada. A conversa falhava antes do primeiro turno, mesmo no workspace operacional vazio.
- O modelo padrão GPT-5.5 foi recusado pela conta ChatGPT usada no teste. O catálogo da conta indicou GPT-6.1 Sol como padrão disponível.
- O SDK antigo não reconhecia a classificação `modelNotFound`. Ele convertia a conclusão com erro em uma notificação desconhecida, deixando o consumidor do stream aguardando indefinidamente.

O status de instalação, configuração e login era positivo porque essa verificação não executa um turno completo.

## Correção

- Desativar a descoberta de instruções de projeto apenas no perfil operacional, com `project_doc_max_bytes=0`. As instruções continuam sendo fornecidas pelo backend. A configuração está descrita na [referência oficial](https://learn.chatgpt.com/docs/config-file/config-reference).
- Usar GPT-6.1 Sol como modelo base do console e migrar o GPT-5.5 legado no backend e na preferência salva do painel. A política de promoção por categoria continua explícita; o contrato de modelos foi atualizado conscientemente.
- Adaptar classificações de erro desconhecidas para `other` antes da validação do SDK, preservando mensagem, estado de falha e indicação de nova tentativa. Aplicar a adaptação também às notificações para que a conclusão do turno seja reconhecida.
- Exibir a mensagem textual dos erros estruturados da API no painel.

O perfil continua negando acesso à raiz e mantendo rede, shell, execução, aplicativos e MCPs herdados desativados. O workspace operacional continua separado por cliente, usuário e conversa.

## Validação

- Reprodução do erro de inicialização com o runtime local, seguida de criação de thread bem-sucedida com o ajuste.
- Turno real com uma saudação sintética concluído em aproximadamente quatro segundos, com resposta não vazia e sem acesso a dados comerciais.
- Fluxo completo de criação e execução concluído em aproximadamente 14 segundos com cliente fictício e arquivos temporários: modelo legado migrado, seletor e respondente reais executados, resposta não vazia e nenhuma ferramenta solicitada. O seletor escolheu responder sem dados comerciais.
- 168 testes Python aprovados, incluindo contratos, permissões, isolamento, evidências incompletas e conflitantes, prompt injection, timeout, fallback e compatibilidade do SDK.
- Regressão do painel e teste de navegador sem interface aprovados, incluindo migração da preferência antiga.
- Compilação dos Python alterados e verificação de whitespace aprovadas.

A alteração foi preparada em Worktree dedicado. A aplicação ao checkout principal depende da segunda aprovação prevista no projeto.
