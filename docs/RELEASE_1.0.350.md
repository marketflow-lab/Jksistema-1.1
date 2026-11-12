# JK Sistema 1.0.350

Atualização pelo canal `app-update` com as correções de custos e margens de Importações.

- Persiste os resultados do valor unitário e restaura os valores antes de aguardar a rede.
- Consulta a base fiscal dos SKUs em lote, com cache por revisão dos dados.
- Atualiza o último custo cadastrado quando suas fontes mudam, compartilhando o cache entre lista e detalhe.
- Exibe na lista a mesma margem pelo custo simulado da importação usada no detalhe do SKU.
- Mantém a fila de análises após o antigo limite de dois minutos e preserva resultados válidos durante falhas temporárias.
- Preserva custos, complementos e análises salvas durante a navegação e as atualizações entre abas.
- Inclui os anúncios da loja e suas variações, com preços e margens vinculados ao SKU e à conta corretos.

O `app-update` reutiliza o runtime compatível existente. O Setup completo acompanha a mesma release.
