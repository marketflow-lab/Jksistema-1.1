# JK ML Collector Local

Extensao local carregada pelo Electron na sessao do Mercado Livre.

Ela roda dentro da pagina do Mercado Livre, mantem um cache em
`window.__JK_ML_COLLECTOR__` e publica um pacote enriquecido no bridge
`#jk-ml-collector-data`, que o modulo `avant` le pelo navegador interno.

Objetivo do MVP:

- coletar cards visiveis da busca;
- exibir um card "Informacoes JK" dentro de cada anuncio da busca;
- observar mudancas do DOM;
- capturar respostas de `fetch` e `XMLHttpRequest` quando a pagina ja carrega dados dos anuncios;
- consultar detalhes em segundo plano pelo service worker da extensao, sem navegar nos links dos anuncios;
- entregar dados normalizados sem depender do AvantPro.

Esta extensao e local do sistema. Nao envia dados para servidores externos.
