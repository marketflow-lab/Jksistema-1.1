# JK Sistema 1.0.139

O carregamento da aba Perguntas passa a mostrar as lojas e perguntas progressivamente. Fotos, títulos, SKUs e detalhes são completados em segundo plano, com cache por sessão e loja.

- Troca de loja durante consultas, descartando resultados atrasados.
- Paginação de 20 perguntas com reaproveitamento dos blocos consultados.
- Atualização preservando pergunta selecionada, foco e rascunho.
- Contadores separados do carregamento de anúncios e histórico.
- Preservação das conexões na sincronização entre máquinas.
- Treinamento organizado por loja e SKU, com orientações integradas ao Obsidian.
- Capas de anúncios do Mercado Livre salvas por loja.

Os ganhos de tempo documentados em `PERGUNTAS_LOADING_PERFORMANCE.md` foram medidos com dados simulados. O tempo real depende das lojas e da resposta do Mercado Livre.

## Validação da release

- 598 testes Python aprovados e dois casos de symlink ignorados por limitação do ambiente.
- 123 arquivos de testes Node aprovados, incluindo atualização simulada de 1.0.99 para 1.0.139.
- 19 arquivos Python alterados compilados sem produzir bytecode.
- Contratos, lockfiles, espelhos HTML e manifesto editorial conferidos.
- Verificadores de fonte e pacote aprovados, com paridade dos arquivos de backend e interface.
- Manifesto de atualização conferido contra o tamanho e SHA512 do instalador.
- Instalação offline, importação do backend, consistência das dependências e reutilização do runtime aprovadas em diretório temporário isolado.

## Escopo técnico da publicação

Esta release distribui o aplicativo desktop e seu runtime offline. A migração opcional de contas legadas para a Central depende do contrato correspondente no serviço remoto; a publicação no GitHub não implanta esse serviço. Na verificação desta release, o serviço público respondeu ao health e ao bootstrap existentes, mas ainda não expôs a nova consulta de migração. O fluxo legado permanece disponível e a migração depende de habilitação enviada pelo serviço.
