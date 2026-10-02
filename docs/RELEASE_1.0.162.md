# JK Sistema 1.0.162

Atualização pequena (app-update).

- Exibe os itens da lista de Importação assim que o detalhe chega; preferências de colunas e metragem complementar carregam em paralelo.
- Usa a metragem contextual já presente nos itens e busca complementos apenas no cadastro da loja exata. Mantém indisponibilidade explícita e permite nova tentativa.
- Preserva edição, foco, fornecedor, arraste e escolhas de colunas durante respostas tardias. Respostas antigas não substituem o carregamento atual.
- Reutiliza o contexto de cadastro e as resoluções de fotos somente dentro da requisição, com isolamento por cliente e loja e leitura atualizada na próxima abertura.
- Preserva as correções de responsividade, workers e gravação atômica integradas na versão 1.0.161.
