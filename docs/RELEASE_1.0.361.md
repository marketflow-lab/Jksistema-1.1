# JK Sistema 1.0.361

Atualização pequena pelo canal `app-update`, com proteção da leitura concorrente de listas e estabilização das verificações de navegador.

- A leitura do arquivo de listas usa o mesmo lock da gravação durante abertura, leitura e fechamento. Isso evita que um upload encontre o JSON aberto por outro leitor no Windows. O parsing permanece fora desse trecho, e os conflitos de edição e o isolamento por cliente continuam verificados.
- O teste do editor de Médias aguarda a lista correta, os itens carregados e a liberação da edição antes de comparar os SKUs reabertos.
- Os testes de autenticação controlam os prazos dos cenários que medem deadline e formulário desabilitado. Cancelamento explícito e troca de contexto preservam as verificações de uma única submissão.
- A verificação de layout da Invoice readquire o cartão após uma atualização da lista e mantém todas as asserções de geometria. O teste de cache de margens aguarda a carga completa antes de avançar o relógio entre os cenários.
- O teste do balão de pedidos estabiliza viewport e scroll antes do hover, mantendo a expiração de cinco segundos e a rejeição de respostas tardias.

O planejamento de compras da versão 1.0.360 integra esta base. O contrato do runtime permanece igual; o Update reutiliza o runtime compatível existente. O workflow verifica o Setup completo e o Update antes da publicação.

Validação local: 332 testes Python aprovados e um teste de junction ignorado pela permissão do ambiente; oito arquivos de regressões de navegador, incluindo 45 casos de autenticação, 11 cenários de hover e oito cenários de Invoice; nove verificações de release e empacotamento aprovadas. Locks de dependências, manifesto do contexto, 25 espelhos HTML, contrato de runtime, sintaxe JavaScript, compilação Python e `git diff --check` conferidos.

As quatro versões de pacote, os contratos de versão e o manifesto do bundle de contexto acompanham a 1.0.361. A publicação usa `app-update` com `publish=true`; a cópia instalada não é atualizada por esta tarefa.
