# Validação da orientação de Perguntas v10

Base: `2974238ad3dc27efd110c6d2141dfc8def288e3e`.

## Escopo

- Política compartilhada aplicada aos caminhos públicos de análise, redação e contingência, inclusive os retornos antecipados do V18 e o redator final de compatibilidade.
- Assinatura canônica, produtos novos e condições comerciais mantidos. Uso de características OEM condicionado à equivalência comprovada.
- Política de pós-venda preservada. Rotas, aliases, schemas e exports públicos preservados.
- Versão da política incrementada para v10 e hash de contexto atualizado. Versão do aplicativo permanece 1.0.140.
- Novo módulo incluído nos requisitos de distribuição e de paridade entre fontes e pacote.

## Verificações

As regressões novas exercitam os construtores reais de prompts, inclusive com contexto V18, dados sintéticos insuficientes e alternativas, tentativa de instrução inserida na pergunta e fronteira entre pergunta pública e pós-venda. Elas verificam a entrega da orientação, os contratos de saída e a preservação de assinatura e contexto. Não constituem uma avaliação de respostas de um modelo ao vivo.

A suíte ampliada inclui casos de ausência de evidência, fontes conflitantes, isolamento de loja, prompt injection, timeout e contingência. Foram verificadas também a compilação Python, a integridade do diff, as dependências dos módulos e os hashes dos contratos. Somente os hashes de política e prompts foram atualizados.

## Falhas preexistentes confirmadas na base

Treze casos falharam também no checkout original, sem a alteração da orientação:

- Quatro casos de `test_perguntas_compatibility_alternatives_v1.py`: contingência por timeout, resposta em branco e dois contratos antigos de acréscimo de assinatura pelo código.
- Um caso de `test_rvc_seller_behavior_v6.py`: helper de configuração do pós-venda ausente.
- Um caso de `test_perguntas_ai_only_agent_routing.py`: inicialização de dependências de configuração/logging.
- Um caso de `test_black_jhon_honda_fit_public_replay_v16.py`: assinatura esperada no replay.
- Dois casos de `test_perguntas_seller_voice.py`: cliente sintético sem `sku_question_context`.
- Quatro casos de `test_ppv_catalog_context.py`: contexto exato e contingência de compactação do pós-venda.

Esses casos não foram alterados para acomodar a mudança. A comparação com a base distingue essas falhas de regressões introduzidas nesta correção; a suíte ampliada completa não está totalmente verde.

## Ativação

O diff é preparado em Worktree dedicado. A incorporação ao checkout depende da segunda aprovação prevista no AGENTS.md. A instalação usa o código empacotado: reiniciar o pacote atual não carrega o código do checkout. A ativação na instalação requer atualização pelo fluxo de empacotamento e instalação, seguida de reinício do backend. Não se deve copiar arquivos avulsos sobre a instalação.
