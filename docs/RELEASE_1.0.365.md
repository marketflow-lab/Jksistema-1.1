# JK Sistema 1.0.365

Atualização pequena pelo canal `app-update`, com as correções aprovadas de Médias e Pedidos, BlackJohn e Sala de reunião.

- Médias e Pedidos mantém o cursor anterior nas células e a soma por seleção. As fotos ampliam ao passar o mouse ou receber foco, inclusive quando chegam depois da tabela. A ampliação preserva a seleção e fecha ao sair da foto, remontar a tabela ou invalidar as fotos.
- BlackJohn evita repetir respostas ao restaurar a conversa e apresenta tabelas legíveis no painel do chat.
- Sala de reunião recebe o layout acolhedor e responsivo aprovado.
- Os testes da configuração de IA de pós-venda e da contagem de rotas acompanham o catálogo central de modelos já adotado pelo sistema, preservando os hashes dos contratos de Vendas.

Validação: seleção e cópia de células, soma, ampliação das fotos, mídia autenticada, carregamento e rolagem com até 2.000 produtos, regressões do BlackJohn, layout da Sala de reunião e contratos de versão e distribuição.

Versões dos pacotes e lockfiles, manifesto de contexto e contratos de versão alinhados em 1.0.365. Publicação pelo workflow `desktop-release.yml` com `release_mode=app-update` e `publish=true`, incluindo os gates de Setup, Update e reutilização do runtime. O contrato de runtime permanece compatível.
