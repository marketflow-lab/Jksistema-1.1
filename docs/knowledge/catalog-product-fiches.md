# Fichas cadastrais por loja

A ficha de Perguntas e Pós-Vendas passa a exibir os dados disponíveis no cadastro da própria loja mesmo quando o SKU ainda não possui anúncio vinculado. A ausência de orientação editorial deixa de ocultar esses dados.

## Fontes e identidade

O catálogo resolvido de cada loja fornece somente campos técnicos explicitamente cadastrados: nome, descrições, marca, categoria, códigos de produto, dimensões, peso, imagens e características estruturadas. Não são inferidas características a partir de descrições. Preço, estoque, custo, NCM, CEST e outros tributos permanecem operacionais.

A identidade inclui cliente, `store_id`, vendedor, site e SKU. Zeros iniciais são preservados. O legado segue as mesmas associações comprovadas do Cadastro; fontes sem associação resolvida são listadas como pendências.

## Publicação e recuperação

`catalog_product_repository` mantém tabelas próprias no banco do Context Hub. Cada revisão possui um manifesto imutável e objetos de origem em `70_Gerado/CadastroPorLoja`. A troca da geração ativa ocorre em uma transação SQLite. Objetos inalterados são reutilizados, e a revisão anterior pode ser recuperada com `rollback_catalog_snapshot`.

O índice de navegação do Obsidian é escrito atomicamente após a transação. Se essa escrita falhar, a próxima tentativa recompõe o índice; as consultas usam o manifesto ativo e verificam identidade e hash de cada documento. Objetos gerenciados ausentes são recuperados da mesma revisão verificada durante a sincronização. Um arquivo gerado alterado manualmente é preservado e sinalizado como indisponível, evitando consumir uma origem adulterada. As edições da ficha continuam em `80_Curadoria`, com valor original e revisão separados. Uma atualização da origem conserva o valor editado e indica a divergência.

A publicação geral do Context Hub preserva essa área independente, incluindo seu histórico, durante troca de geração, rollback e recuperação. Ausência em uma consulta nunca exclui uma ficha. Somente tombstones explícitos retiram produtos da consulta ativa; orientações e histórico permanecem.

## Sincronização

A fila persistente `catalog-sync.db` agrupa eventos por cinco segundos. Salvamento, lote, exclusão e sincronização entre máquinas notificam somente após o commit. Cada loja tem exclusão mútua entre processos; novos eventos durante uma execução ficam pendentes para a seguinte.

Na inicialização e a cada 15 minutos, a reconciliação confere novamente o cadastro. Isso cobre reinícios e interrupções entre o commit operacional e a notificação. Falhas retornam códigos sem conteúdo privado, mantêm a pendência e permitem nova tentativa.

`GET /api/mercadolivre/ia-treinamento/sincronizacao` consulta o estado. `POST` solicita atualização com `store_id` e SKU opcional, sob a autenticação e validação de loja existentes. Solicitações de SKU são agrupadas na atualização da loja para não perder alterações concorrentes.

## Interface e IA

Os botões **Atualizar informações** e **Sincronizar fichas da loja** acompanham a fila. A tela diferencia falta de orientação, falta de dados, espera e falha. Consultas possuem timeout e invalidam respostas de lojas ou SKUs anteriores. Rascunhos e identificadores dos campos editados sobrevivem à atualização.

A IA recebe o cadastro como fonte separada, integral e sem autoridade instrucional. O acesso exige vínculo exato previamente validado ou prova interna derivada do anúncio oficial; dados fornecidos na solicitação ou enriquecimento local não comprovam identidade. No pós-venda, também é verificada a linha do pedido, incluindo vendedor e variação. Conhecimento técnico existente é preservado, conflitos exigem confirmação independente e orientações públicas não viram instruções de pós-venda. Falha ou excesso de tamanho mantém o fluxo conservador de indisponibilidade.

## Carga inicial de 9 de setembro de 2026

| Loja | SKUs / fichas projetadas | Com algum dado técnico | Sem dado técnico na fonte | Com descrição | Fichas vinculadas anteriores |
| --- | ---: | ---: | ---: | ---: | ---: |
| JK Peças | 787 | 720 | 67 | 337 | 114 |
| Uai Mineirinho | 488 | 448 | 40 | 252 | 101 |
| Carlos José | 404 | 397 | 7 | 244 | 87 |

Foram verificadas 1.679 fichas contra o cadastro e a repetição foi idempotente nas três lojas. O SKU `001` da Uai Mineirinho foi validado com nome e descrição, preservando os zeros iniciais. As gerações vinculadas anteriores e o conteúdo integral de `80_Curadoria` foram preservados.

As 1.369 pendências legadas pertencem ao conjunto do cliente e não devem ser somadas três vezes: 1.316 associações com rótulos sem loja atual correspondente, quatro sem loja comprovada e 49 registros sem SKU. Elas não são 1.369 produtos únicos. A lista detalhada contém apenas referência do registro, SKU, origem e motivo.

`scripts/sync_store_catalog_fiches.py` permite inventário somente leitura ou carga com `--apply`, recebendo raiz de dados, cliente, lojas e arquivo de relatório. A ferramenta não inicia o aplicativo. Os artefatos locais desta execução são `output/catalog-initial-load.json` e `output/catalog-validation-consolidated.xml`.

## Cobertura verificada

Resultado consolidado, sem contar novamente testes repetidos: **997 testes Python aprovados e dois ignorados**, além de **três checks JavaScript aprovados**, incluindo execução no Chrome. Compilação dos **34 arquivos Python** alterados ou adicionados e `git diff --check` aprovados. Os dois testes ignorados exigem criação real de links simbólicos, indisponível no Windows deste ambiente; o bloqueio de caminhos com sinalização de link possui também teste independente que passou.

| Área | Cenários cobertos |
| --- | --- |
| Projeção e repositório | Cliente/loja/vendedor/site exatos, SKU `001` versus `1`, ausência de MLB, dados operacionais excluídos, conflito de aliases, idempotência, fonte ausente, falha no meio da materialização, tombstone, rollback, histórico, preservação da curadoria e recuperação de objeto ausente |
| Fila e cadastro | Debounce, eventos concorrentes, reinício sem estado em memória, erro de leitura, eventos durante a execução, hooks somente após commit, lote/importação, fotos da própria loja, Shared Sync e isolamento da identidade anterior |
| Publicação Context Hub | Próxima geração, rollback, recuperação do journal e preservação dos arquivos independentes, inclusive históricos e edições |
| API e editor | Autenticação, rejeição de escopo divergente, campos adicionais proibidos, publicador real até GET de treinamento, falha sem ocultar orientação, valores editados e proveniência, atualização da origem |
| Interface | Dados disponíveis, campos ausentes, erro e nova tentativa, progresso, troca de loja/SKU, descarte de resposta atrasada, rascunho em edição e atualização por loja ou SKU |
| IA pública e pós-venda | Identidade incompleta/forjada, zeros iniciais, variações, pedido de outro vendedor, fontes conflitantes, descrição com instruções maliciosas, DLP, indisponibilidade, recuperação, limites de contexto e preservação integral do catálogo |
| Fronteiras de execução | Worker, perguntas manuais síncronas, protocolo v2 e automação: prova somente do anúncio oficial antes do enriquecimento local |
| Regressão e contratos | Cadastro, importações, compatibilidade legada, sincronização entre máquinas, evidências, geração vinculada, editor, orquestrador e arquitetura/contratos dos endpoints |

Esta é cobertura de cenários e regressão, sem estimativa de porcentagem de linhas. O conjunto integrado inicial teve duas ocorrências resolvidas: atualização consciente da quantidade de exports para os novos endpoints e execução do teste multiprocessado a partir de um arquivo real, exigida pelo método `spawn` do Windows. Os cenários afetados e as alterações finais foram reexecutados com sucesso.

Os hashes de rotas foram atualizados para os dois endpoints adicionais. A resposta anterior de treinamento, aliases e schemas existentes foram preservados. Os testes de IA usam dados controlados e fronteiras de API simuladas; a carga real validou os dados locais sem enviar perguntas, respostas ou mensagens a clientes.

## Entrega

Alteração original preparada no Worktree dedicado a partir de `e3b150b44409d63b8b780e5a423f7f3b878d2a57`, commit `4ed6260838b1827769db50cf1c59781bbf80d8fe`. A versão permanece 1.0.139.

Após aprovação explícita da integração, foi criado outro Worktree sobre `cf31097`, preservando as correções recentes de Cadastro e Central de Contas. A integração ajusta a notificação das fichas para ocorrer após a saída da transação completa e dos locks do Shared Sync. A validação inclui importação normal e entre usuários, rollback por falha final e ausência de notificação sem arquivos alterados.

Validação adicional da integração: 231 testes Python aprovados, um ignorado por indisponibilidade de link simbólico e teste de interface no Chrome aprovado. Os seis novos testes da fronteira de commit do Shared Sync também passaram. Não há alteração de versão ou de contratos públicos adicionais nesta integração.

A preparação para a próxima versão inclui somente código, testes e documentação, sem instalador, publicação ou atualização do aplicativo instalado.
