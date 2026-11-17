# JK Sistema 1.0.359

Atualização pelo canal `app-update` com mais espaço para atender às perguntas.

- A página Perguntas permite rolar o cabeçalho e as contas para fora da área visível. O painel de atendimento ocupa quase toda a altura da janela, ampliando a lista e as respostas.
- A fila e o detalhe mantêm rolagem independente. Ao alcançar o fim de uma coluna, a roda do mouse pode continuar rolando a página.
- As disposições de celular e de janelas baixas continuam acessíveis pela rolagem da página.

As regressões de layout verificam desktops de 1033 × 657 e 1347 × 681, janela baixa e celular, incluindo acesso ao painel, altura útil e ausência de rolagem horizontal. A verificação visual com dados sintéticos também conferiu a roda do mouse e uma janela de 850 × 650.

Os arquivos de versão, locks, contratos de versão e manifesto do bundle de contexto acompanham a versão 1.0.359. O contrato do runtime permanece igual; o pacote Update reutiliza o runtime compatível existente. Os artefatos passam pelas verificações do workflow Desktop release antes da publicação.

Validação local: seis testes Python de distribuição e versão e sete testes de release, staging, materialização e atualização simulada aprovados. Locks, manifesto de contexto, espelhos HTML, contrato de runtime, configuração do Update, sintaxe JavaScript, compilação dos testes Python alterados e `git diff --check` conferidos.
