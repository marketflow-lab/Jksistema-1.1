# JK Sistema 1.0.161

Data: 2 de outubro de 2026.

- Mantém as páginas e abas responsivas durante operações demoradas de Importações, catálogo, cálculos e cadastro de fornecedores, com duas operações pesadas simultâneas e espera assíncrona.
- Reduz consultas repetidas do WhatsApp: filtra aprovações elegíveis, consulta os candidatos em lote somente leitura e revalida antes de decidir ou enviar.
- Protege gravações concorrentes das listas com comparação do snapshot, uma recomputação e conflito 409 quando necessário. JSON e cache de downloads são substituídos atomicamente.
- Reduz o trabalho repetido do Context Hub com leitura somente leitura das configurações e cache do inventário estático limitada a quatro entradas e 64 MiB, preservando dados atuais por cliente e os fluxos de publicação e rollback.
- Distribuição por `app-update`, mantendo os contratos da IA, rotas públicas, pins de runtime e dependências da versão 1.0.160.
