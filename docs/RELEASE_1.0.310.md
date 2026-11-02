# JK Sistema 1.0.310

Atualização pequena no perfil `app-update`.

- Etiquetas: engrenagem em cada card abre a configuração atual da impressão, com seleção, arraste, redimensionamento, rotação e medidas em milímetros para etiqueta original, SKU e DANFE.
- O editor preserva a configuração salva por cliente, usuário e loja. As posições permanecem visíveis durante o carregamento ou uma falha da prévia.
- A prévia e o PDF usam os mesmos componentes; abrir e salvar o padrão sem editar preserva a impressão existente. O código DANFE integrado pode ser separado pela seleção explícita de sua área, preservando o arquivo enviado.
- O carregamento identifica servidores sem as rotas novas e orienta reiniciar, em vez de deixar o editor vazio com `Method Not Allowed`.
- Importações: dados e margens do SKU carregam durante a atualização da lista, preservando os dados salvos e a edição enquanto as fontes complementares respondem.
- As consultas ao Mercado Livre e os workers mantêm isolamento de cliente, loja e execução, sem bloquear as rotas de saúde, autenticação e navegação.

Os contratos de runtime e as dependências permanecem iguais aos da versão anterior. A opção Amazon mantém a prévia de amostra; seu processamento ainda não está disponível.
