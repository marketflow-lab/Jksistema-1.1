# JK Sistema 1.0.354

Atualização pequena pelo canal `app-update`, com as correções aprovadas da Pesquisa de Mercado e do Avant Pro.

- Consulta até quatro anúncios simultaneamente em navegadores independentes.
- Reconsulta apenas anúncios com informações pendentes e mostra o progresso dessa fila.
- Preserva resultados concluídos fora de ordem ao pausar, retomar ou solicitar login.
- Corrige a busca da extensão Avant Pro na pasta de recursos do instalador.
- Atualiza a extensão Avant Pro incluída no Setup e no Update para a versão 7.33.5.

Inclui as correções anteriores publicadas até a versão 1.0.353, incluindo Vendas, Devoluções, Importações e navegação em novas abas. O `app-update` reutiliza o runtime compatível existente; o Setup completo acompanha a mesma release.

Validação: instalação limpa sem extensão no Chrome, carregamento real da extensão 7.33.5 no Electron 28.3.3 e testes de paridade dos arquivos do Update.

Limitação conhecida do código original da extensão 7.33.5: uma regra de rede de subframes usa uma constante que o Electron 28 não fornece. Essa regra gera um erro no background, com o service worker permanecendo ativo. A mesma limitação ocorre ao carregar essa versão diretamente do Chrome; os arquivos do fornecedor foram preservados.
