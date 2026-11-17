# JK Sistema 1.0.358

Atualização pequena pelo canal `app-update`, com a correção do login do Avantpro no navegador interno.

- O carregamento ignora versões do Avantpro que dependem de `chrome.sidePanel`, recurso indisponível no Electron usado pelo JK. Quando as versões do Chrome exigem esse painel, o sistema seleciona a versão compatível já incluída no aplicativo e mantém o login dentro da página.
- As versões compatíveis instaladas no Chrome preservam a prioridade existente. A seleção também verifica diretórios explícitos e a extensão incluída no pacote, sem modificar a instalação do Chrome.
- O contrato de testes de fotos de Vendas reconhece as 23 rotas atuais e verifica a rota POST de configuração da atualização automática, acrescentada na versão anterior.
- As correções aprovadas de conversas anteriores já publicadas permanecem incluídas; cópias históricas de Worktrees não são reaplicadas.

Os quatro arquivos de versão, os contratos de versão e o manifesto do bundle de contexto são atualizados em conjunto. O contrato de runtime permanece igual; o Update reutiliza o runtime compatível existente. O workflow também verifica e disponibiliza o Setup completo.

O formulário de autenticação foi verificado vazio em navegador isolado, sem envio de credenciais. Provas isoladas no Electron confirmaram o background funcional da extensão compatível e a falha da extensão que depende do novo painel. A verificação visual online no Electron foi bloqueada pela revisão automática da ferramenta e permanece pendente.

Validação local: 233 testes Python aprovados e um teste de junction ignorado por falta dessa permissão no ambiente; 12 arquivos de regressões JavaScript da integração Avantpro, incluindo 45 casos de autenticação; nove verificações de release e empacotamento aprovadas. Locks de dependências, contrato de runtime, 25 espelhos HTML, manifesto de contexto, compilação Python, sintaxe JavaScript e `git diff --check` conferidos.

A aplicação no ambiente de desenvolvimento e a publicação foram autorizadas. A cópia instalada não é atualizada por esta tarefa.
