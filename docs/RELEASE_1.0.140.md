# JK Sistema 1.0.140

Esta versão reforça o isolamento por loja e melhora o Cadastro, as Perguntas e a sincronização entre máquinas.

- Cadastro com autenticação estável nas rotas protegidas e carregamento consolidado de todas as lojas.
- Tratamento seguro de respostas inválidas, timeout, cancelamento ao trocar de loja, progresso e resultados parciais no Cadastro.
- Nova aba Solicitação em Perguntas, com status e conclusões da IA separados por loja.
- Fichas cadastrais por loja e SKU sincronizadas com o Context Hub e o Obsidian.
- Recuperação da sincronização compartilhada do Cadastro e preservação das conexões das lojas.
- Recuperação manual de migrações e autoridade central de tokens.
- Promoções sem cupons elegíveis passam a informar o motivo e não incluem campanhas incompatíveis.

## Validação da release

- 5.035 testes Python e 58 subtestes aprovados; 13 casos de symlink ignorados por limitação de permissão do Windows.
- 129 arquivos de testes Node verificados, incluindo o fluxo de atualização empacotada.
- 70 testes do gateway do WhatsApp e compilação TypeScript aprovados.
- Contratos do Bug Hunter e cinco sondas de diagnóstico aprovados sem mutações externas.
- Lockfiles, espelhos HTML, manifesto editorial, código Python e pacote conferidos.
- Runtime offline, conteúdo empacotado, atualização local simulada e instalação isolada verificados.

## Escopo técnico da publicação

A listagem de produtos usa os dados locais já sincronizados. O agrupamento de todas as lojas preserva o `store_id` exato e não compartilha registros, SKUs ou conhecimento entre lojas.
