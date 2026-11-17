# JK Sistema 1.0.362

Atualização pequena pelo canal `app-update`, com o indicador de carregamento da Trend Minas no gráfico de Vendas e a correção aprovada do chat BlackJohn.

- Ao trocar loja ou período, o símbolo da Trend Minas pulsa sobre o gráfico nos dois layouts. O painel e o fundo são translúcidos, conforme a prévia aprovada.
- O indicador acompanha a leitura dos filtros atuais, inclusive quando o gráfico salvo aparece antes da atualização pela rede. Respostas de seleções anteriores não encerram o carregamento da seleção atual.
- Sucesso, erro, tempo limite, acesso negado e cancelamento da sessão encerram o indicador. A renovação de sessão permite uma nova consulta sem manter uma animação antiga ativa.
- A área continua acessível em telas estreitas e respeita a preferência por movimento reduzido.
- O teste de cache aguarda o início da leitura do resumo antes de conferir que o gráfico consegue terminar com essa resposta ainda pendente, sem pressupor a ordem de início das duas requisições.
- O BlackJohn volta a iniciar conversas com o runtime Codex atual: usa o modelo base disponível, migra a preferência legada e reconhece conclusões com classificações de erro novas sem ficar aguardando indefinidamente. O painel também mostra a mensagem textual devolvida pela API.
- O perfil operacional do BlackJohn mantém o isolamento e as restrições existentes, recebendo suas instruções do backend sem acionar a descoberta de documentos de projeto pelo auxiliar incompatível do sandbox Windows.

Esta versão parte da 1.0.361 e preserva as correções aprovadas de Médias e Pedidos, Pesquisa de Mercado, Perguntas e demais módulos já consolidadas nessa base. O contrato de runtime permanece igual; o Update reutiliza o runtime compatível existente.

Os quatro arquivos de pacote, os contratos de versão e o manifesto do bundle de contexto acompanham a 1.0.362. A publicação usa `release_mode=app-update` e `publish=true`.

Validação local: sete scripts de regressão de Vendas aprovados, incluindo 55 cenários de confiabilidade e verificações de navegador dos dois layouts, filtros, cache, respostas tardias e encerramento da sessão; seis testes Python de distribuição e versão aprovados. Dez verificações de dependências, espelhos HTML, manifesto, contrato de runtime, pacote Update, perfis, materialização, bootstrap, extração WiX e staging de artefatos aprovadas. Sintaxe JavaScript, compilação dos Python alterados e `git diff --check` conferidos.

A consolidação do BlackJohn foi validada com mais 95 testes Python, as regressões de código e navegador do painel e a compilação dos módulos alterados. Os testes verificam ausência de evidência, fontes conflitantes, prompt injection, isolamento de cliente e loja, tempo limite e fallback.
