# Consulta de diretrizes oficiais nas respostas de Perguntas

Base: `efeb28516d6f8308a77f73e0e59a9aed9ed84340`.
Branch: `codex/perguntas-diretrizes-ml-20260916`.
Contrato interno: `jk_ml_official_policy_research_v1`.

## Comportamento

Antes da primeira chamada de redação ou análise, dúvidas sobre cancelamento, reembolso, devolução e procedimentos/políticas do Mercado Livre recebem pesquisa dedicada em páginas oficiais atuais. O resultado acompanha as etapas seguintes, inclusive perguntas simples V18, perguntas mistas, compatibilidade, revisão e contingência. Uma nova geração refaz a consulta; mudança de tópico ou site invalida o resultado anterior.

A classificação continua feita pela IA. O classificador pode registrar `politica_oficial_ml` no campo existente `required_evidence`, sem alteração do schema. Indicadores de assunto também antecipam a coleta, mas não decidem categoria, fluxo, compatibilidade, direitos nem resposta. “Se não servir, posso devolver?” sem relato de compra existente continua como dúvida anterior à compra. Procedimentos de pagamento, envio, reclamação/mediação e garantia têm consultas fixas específicas.

A consulta separa política geral da situação autenticada de cada pedido. Não autoriza operação nem envio. As portas de geração/automação de pós-venda atualmente desativadas continuam desativadas; funções internas de rascunho recebem a orientação quando chamadas por um fluxo permitido.

## Orientação aplicada à IA

Consultar as diretrizes oficiais antes de orientar sobre procedimentos. Conferir país/site, categoria do produto, condições, etapa da compra e forma de pagamento. Uma página lida não garante que o trecho responda ao caso. Usar apenas o que for pertinente e comprovado, sem transformar prazo condicional em prazo universal ou prometer cancelamento, troca, devolução ou reembolso aprovado/realizado.

Se houver conflito de fontes, ausência de documento, timeout ou condição decisiva desconhecida, responder os fatos confirmados e indicar os detalhes/ajuda da própria compra, sem inventar menus ou etapas. Não afirmar pesquisa ou confirmação que não ocorreu. Preservar a assinatura da loja e responder todas as partes pertinentes da pergunta; não inserir chamada de compra em orientação sobre problemas/políticas.

## Fontes, limites e isolamento

- Somente site explícito `MLB`, coerente entre identidade e item/contexto. Idioma, nome da loja ou texto do comprador não determinam país. Site ausente, divergente ou não suportado não recebe regra brasileira por suposição.
- Consultas formadas exclusivamente de tópicos fixos, sem comprador, pedido, SKU, histórico, cookies ou credenciais. A busca pública não cria cache de tenant.
- Allowlist exata HTTPS de `www.mercadolivre.com.br` (`/ajuda/`, `/compra-garantida`) e `vendedores.mercadolivre.com.br` (`/ajuda/`, `/aprender/`, `/nota/`). Snippets de busca não contam como página consultada.
- DNS público verificado, conexão fixada ao IP verificado e TLS validado para o domínio. Redirecionamentos, destinos privados e conteúdo não textual são recusados.
- Máximo de duas consultas, três tentativas de leitura e 18 segundos por coleta; até 512 KB por página e 16 mil caracteres sanitizados por documento. A fila de execução tem capacidade limitada, inclusive quando um provedor demora a encerrar.
- Resultado inclui status, tópicos solicitados/cobertos, URL e data UTC de consulta. Cobertura é de coleta, não decisão de aplicabilidade. Dados de páginas são `UNTRUSTED_REFERENCE_DATA`, separados das instruções da aplicação.

Pontos de referência públicos: [Ajuda do Mercado Livre](https://www.mercadolivre.com.br/ajuda/) e [Compra Garantida](https://www.mercadolivre.com.br/compra-garantida). Nenhum prazo dessas páginas foi gravado como regra universal no código.

## Validação e limites

Testes do coletor simulam rede, páginas, ataques, conflitos, ausência de evidência e timeout. Testes do fluxo exercitam o transporte real para o modelo com o provedor substituído, verificando a ordem pesquisa → redação, a preservação do envelope V18 e o isolamento entre gerações/tenants. Não são uma avaliação de respostas de um modelo ao vivo.

Resultado final: **420 testes aprovados**, em dois lotes sem sobreposição: 341 de fluxo, classificação, orientação pública, bloqueios de pós-venda, contratos, V18 e limites de resposta; 79 do coletor oficial. Os arquivos novos `test_marketplace_policy_flow.py` e `test_marketplace_policy_sources.py` contêm 112 desses casos. Compilação dos 12 arquivos Python alterados e `git diff --check` concluídos. As versões de `package.json` e `electron_app/package.json` continuam `1.0.140`.

O teste manual de leitura real acessou uma página oficial de Ajuda com DNS/TLS verificados. A primeira execução avulsa do coletor encontrou o logger do backend sem inicialização e reportou indisponibilidade. Após inicializar somente o logger com `NullHandler`, a busca e a leitura reais do coletor completo retornaram `available`, com três páginas de `www.mercadolivre.com.br`, sem mocks, dados de compradores/pedidos ou cache por tenant. Isso comprova a coleta; não é uma avaliação da resposta de um modelo ao vivo nem confirmação da elegibilidade de um pedido.

O hash do contrato de prompts e o marcador de contexto do orquestrador foram atualizados conscientemente. Rotas, schemas e exports públicos permanecem compatíveis. Os dois módulos novos constam do manifesto de recursos para a próxima distribuição. A versão do aplicativo permanece inalterada; não houve build, instalador, publicação nem atualização da instalação.
