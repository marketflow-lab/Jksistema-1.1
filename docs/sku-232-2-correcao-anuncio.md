# Revisão do anúncio do SKU 232-2

Rascunho para revisão, sem publicação. Aplicável somente ao par preto do SKU
232-2 no anúncio MLB2781314798. O operador deve conferir a identidade completa
do cliente, loja, seller e site pelo cadastro antes de alterar o anúncio.

## Campos sugeridos

| Campo | Valor revisado |
| --- | --- |
| Título | Par Puxador Interno Traseiro BMW Série 3 F30 F31 Preto |
| Posição | Portas traseiras |
| Lados | Esquerdo e direito |
| Cor | Preto |
| Material | Plástico ABS |
| Quantidade | 2 unidades, um puxador de cada lado |
| Referências de aplicação | 51427281465 (traseiro esquerdo preto); 51427281466 (traseiro direito preto) |
| Aplicação da BMW 318d | F30 sedã e F31 Touring, antes e depois da reestilização (LCI) |

Manter a marca da reposição já cadastrada. As referências BMW servem para
identificar a aplicação e não indicam que o produto anunciado seja original BMW.
Excluir desta variação os códigos dianteiros 51417279311, 51417279312,
51417279315 e 51417279316, e os códigos traseiros de outra cor 51427281469 e
51427281470. Preservar as demais variações do vendedor.

## Descrição sugerida

Par de puxadores internos das portas traseiras para BMW Série 3 F30 sedã e F31
Touring, na cor preta. O conjunto contém um puxador traseiro esquerdo e um
puxador traseiro direito em plástico ABS.

Aplicação da BMW 318d: gerações F30 e F31, incluindo versões anteriores e
posteriores à reestilização (LCI). A aplicação é identificada pela geração do
veículo; o nome 318d, isoladamente, não identifica a carroceria.

Referências de aplicação do par preto:

- 51427281465: puxador traseiro esquerdo.
- 51427281466: puxador traseiro direito.

As referências indicam a aplicação da peça de reposição. O conjunto corresponde
aos puxadores internos traseiros usados como apoio para puxar e fechar as portas.

## Evidências e limites da revisão

- [Catálogo oficial BMW, referência 51427281465](https://shop.bmw.ca/p/BMW__340i/Support--pull-handle--rear-left-SCHWARZ/43751594/51427281465.html): suporte do puxador traseiro esquerdo preto, com aplicação 318d sedã e Touring.
- [RealOEM, BMW 318d F31 produzida em junho de 2013, diagrama 51_8529](https://www.realoem.com/bmw/enUS/showparts?id=3K11-EUR-06-2013-F31-BMW-318d&diagId=51_8529): par preto 51427281465 e 51427281466, inclusive na F31 anterior à reestilização.
- O vínculo com o SKU provém da identificação já curada do produto como par
  traseiro preto F30/F31. Um catálogo externo confirma a aplicação dos códigos;
  isoladamente, não identifica o conteúdo do estoque do vendedor.
- As faixas genéricas 2011–2015, 2014–2018 e 2014–2019 do anúncio não são usadas
  como limites de aplicação nesta revisão. O caso F31 2013 comprova uma aplicação
  concreta, sem estabelecer por si só todos os anos inicial e final.

## Atualização pontual do dossiê

A função pura `repair_sku_232_2_dossier` em
`scripts.review_sku_dossiers_ptbr` recebe o JSON atual e devolve uma cópia com a
correção. A função valida o SKU literal, completa o par de referências, substitui
as antigas linhas gerais F30/F31 pelas gerações sem faixa anual e explicita a
318d pré-LCI/LCI. Mantém medidas, material, instalação, orientações e outros
modelos existentes. O indicador de cobertura de anos passa a ser falso porque os
anos não foram documentados como intervalos.

O chamador deve conferir o escopo operacional completo antes da persistência.
Passar `updated_at` aplica o novo timestamp somente se o conteúdo mudar. A
reexecução sobre o dossiê corrigido é idempotente. Não executar a regeneração em
lote para corrigir um único produto: usar o fluxo pontual, com publicação atômica
da projeção consumida pela IA.
