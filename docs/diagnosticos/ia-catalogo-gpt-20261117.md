# Modelos GPT e níveis de raciocínio no JK Sistema

Data: 17/11/2026.

## Comportamento

Configurações, BlackJohn, Vendas, Perguntas, Pós-venda e WhatsApp usam um catálogo comum de modelos GPT e suas capacidades. O catálogo Codex é descoberto pelo runtime autenticado e preserva os níveis anunciados por cada modelo. A consulta é limitada no tempo, compartilhada entre leitores e mantém a última resposta válida durante indisponibilidade.

O catálogo da OpenAI API é separado e consulta os modelos disponíveis na credencial configurada, com cache e timeout. O fallback local indica disponibilidade não confirmada. Modelos de áudio, tempo real e outros transportes incompatíveis não são oferecidos no fluxo de texto existente.

O sistema reconhece `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` e `ultra`. Os seletores oferecem apenas os níveis aceitos pelo modelo e transporte. A API direta segue as capacidades documentadas; `ultra` é oferecido no Codex quando anunciado pelo runtime.

As cinco finalidades de IA possuem configuração de raciocínio: padrão, Perguntas, Pós-venda, chat e Favoritos. A escolha explícita de modelo prevalece sobre promoções automáticas por categoria. As políticas automáticas continuam disponíveis quando não há uma escolha explícita.

O WhatsApp preserva o raciocínio escolhido em configurações, decisões, subtarefas e tentativas. A pesquisa técnica de Perguntas mantém o mínimo deliberado `high`, respeitando o modelo Codex configurado e níveis superiores suportados.

A migração do GPT-5.5 legado permanece restrita ao transporte Codex. Fornecedores e aliases personalizados continuam compatíveis. Modelos salvos temporariamente indisponíveis permanecem visíveis para revisão da preferência.

O perfil operacional continua somente leitura, sem habilitar shell ou ampliar acesso aos dados. O ID duplicado no seletor de raciocínio do BlackJohn foi corrigido, garantindo que o nível escolhido chegue ao backend.

## Validação

- O runtime autenticado anunciou oito modelos: GPT-6.1 Sol, GPT-6 Astra, GPT-6.1 Terra, GPT-6 Sol, GPT-6 Luna, GPT-5.6 Sol, GPT-5.6 Terra e GPT-5.6 Luna.
- Um turno sintético completo com GPT-6.1 Sol confirmou `effort=ultra` no transporte, resposta não vazia e nenhum erro. Descoberta e turno levaram aproximadamente cinco segundos.
- Testes exercitam catálogo dinâmico e futuro, cache, timeout, falhas, separação de provedores, seleção explícita, níveis por modelo, persistência, isolamento, evidência, prompt injection e transporte.
- Testes de navegador verificam `max` e `ultra`, padrão Luna, preferência salva e atualização depois de fallback.
- Os contratos de rotas, modelos, níveis, schema de IA e estágio técnico foram atualizados conscientemente. Os recursos novos constam no contrato de empacotamento.

Referências: [GPT-6 e migração](https://developers.openai.com/api/docs/guides/latest-model), [raciocínio](https://developers.openai.com/api/docs/guides/reasoning) e [GPT-5.6](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6).
