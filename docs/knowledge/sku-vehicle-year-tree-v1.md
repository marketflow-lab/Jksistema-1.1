# Árvore SKU por veículo e ano

> Arquivo gerado. A fonte de manutenção é `info/<client_id>/SKU/*.json`.

- Versão da árvore: `1.0.0`
- Fonte canônica: `info/<client_id>/SKU/*.json`
- Classe da árvore: `generated_secondary`
- Caminho principal no Obsidian: `70_Gerado/Produtos/Veiculos-Compativeis.md`

## Hierarquia

```text
Veículos compatíveis
└── Montadora
    └── Modelo ou aplicação
        └── Ano
            └── SKUs compatíveis
```

## Regras determinísticas

- `AAAA` materializa somente o ano informado.
- `AAAA a BBBB` materializa todos os anos inclusivamente.
- Listas são expandidas item a item; intervalos separados preservam lacunas reais.
- Repetições são removidas pela chave `SKU + montadora + modelo + ano`.
- Valores abertos ou ambíguos não são inferidos sem uma substituição aprovada e versionada.
- A ordenação é estável por montadora, modelo, ano numérico e SKU.
- A árvore não altera nem substitui o dossiê JSON canônico.

## Correções pesquisadas do SKU 214

| Montadora | Modelo ou aplicação | Anos materializados | Estado |
|---|---|---:|---|
| Hyundai | HB20 1.0 e 1.6 com chave mecânica | 2012 a 2025 | vigente; último ano verificado 2025; `ano_final = null` |
| Hyundai | HB20S com chave mecânica | 2013 a 2025 | vigente; último ano verificado 2025; `ano_final = null` |
| Hyundai | HB20X com chave mecânica | 2013 a 2022 | intervalo encerrado em 2022 |
| Hyundai | i20 I (PB/PBT) | 2008 a 2015 | intervalo encerrado em 2015 |

Nos três modelos HB20, continua obrigatória a conferência de que o veículo usa
chave mecânica. Em todas as aplicações deve ser confirmado o código da peça
instalada; conector de seis pinos e referências `93110-3S000` ou `93110-0U000`
devem ser conferidos quando constarem no dossiê. Os anos 2026 e posteriores não
são adicionados automaticamente ao HB20 ou HB20S; uma nova verificação é
necessária.

## Fontes da correção

- DNI2617: https://dni.com.br/pt/dni2617-comutador-de-ignicao-hb20-elantra-sonata-hyundai-kia-93110-3s000-12v/
- Catálogo D'Paula 2025: https://dpaula.com.br/wp-content/uploads/2025/04/dpaula_catalogo_produtos_2025_A.pdf
- Hyundai Bluelink: https://www.hyundai.com.br/bluelink.html
- Catálogo Hyundai HB20X 2021/2022: https://www.hyundai.com.br/content/dam/hmb/cat%C3%A1logos/catalogo_HB20X_evolution_digital.pdf
- Aplicações detalhadas do i20 I: https://www.kmotorshop.com/en/article-detail/view/326751/ignition-switch-eks-hy-002-nty-93110-3s000-2104027-24027
- Meat&Doria 24027: https://www.meat-doria.com/en/product_meat/24027

## Publicação e navegação

- O mapa raiz é dividido em grupos de montadoras para manter o grafo navegável.
- Montadoras com muitos modelos são paginadas sem cortar um modelo ou seus anos.
- Cada parte exibe a sequência completa `modelo → ano → SKUs`.
- Todos os links publicados devem resolver dentro da mesma geração.
- `80_Curadoria` permanece fora da área gerenciada e deve ser preservada.
