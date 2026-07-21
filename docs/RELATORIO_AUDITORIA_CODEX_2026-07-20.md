# Auditoria do uso do Codex no JK Sistema

Data da auditoria: 20 de julho de 2026
Escopo: código-fonte, arquitetura, runtime instalado, Context Hub, integração OpenAI/Codex, ferramentas internas, segurança, testes, telemetria e práticas de desenvolvimento.

## Resumo executivo

O JK Sistema já usa o Codex de forma madura e acima da média. A avaliação geral é **7,5 de 10**.

O sistema possui uma base sólida: integração pelo SDK do Codex e app-server, seleção de dados antes da execução, separação por empresa e loja, catálogo de ferramentas, controle de permissões, Context Hub versionado, aprovação de ações sensíveis, rastreamento de tarefas, cancelamento, histórico, telemetria de tokens e testes relevantes.

Ainda assim, o potencial do Codex não está sendo aproveitado da melhor forma possível. Os principais limitadores não são falta de recursos, mas contratos incompletos entre segurança, contexto, ferramentas e confirmação de resultados. A prioridade deve ser tornar a execução preventivamente segura, ativar gradualmente o MCP nativo já existente, criar avaliações reais de qualidade e adotar uma política explícita para os modelos GPT-5.6.

Há também um risco crítico no provedor direto da OpenAI: uma chamada HTTPS à Responses API desativa a verificação do certificado TLS. O fluxo padrão instalado atualmente usa o login do Codex e modelos Codex, portanto esse trecho não parece ser o caminho principal em produção, mas deve ser corrigido ou bloqueado antes de qualquer uso direto com chave de API.

## Veredito

| Dimensão | Nota | Avaliação |
|---|---:|---|
| Arquitetura geral | 8,0 | Boa separação entre console, agentes, ferramentas e contexto, embora existam módulos muito grandes. |
| Contexto e conhecimento | 8,0 | Context Hub versionado, com publicação manual, rollback, DLP e busca lexical funcional. |
| Ferramentas e integrações | 7,5 | Catálogo amplo e controle no servidor; MCP nativo existe, mas está desativado. |
| Segurança e isolamento | 7,0 | Boas barreiras de empresa, loja e permissão, porém parte do controle de escopo ocorre depois da escrita. |
| Modelos e prompts | 7,0 | Há catálogo GPT-5.6, mas o padrão ainda é GPT-5.5 e faltam avaliações comparativas. |
| Testes e avaliações de IA | 6,5 | Bons testes determinísticos, mas pouca medição da qualidade real das respostas. |
| Observabilidade e custos | 7,0 | Uso e duração são coletados em parte do fluxo; falta um livro-caixa unificado de custo e orçamento. |
| Governança operacional | 8,0 | Aprovações, histórico, cancelamento e políticas específicas estão bem desenvolvidos. |

## Como o Codex é usado hoje

É importante separar três superfícies diferentes:

1. **Codex para desenvolvimento do JK Sistema**: o agente trabalha no checkout Git, examina código, implementa alterações e executa testes.
2. **Assistente operacional dentro do JK Sistema**: usa o SDK Python do Codex e o app-server para responder consultas e operar ferramentas de negócio sob regras de empresa, loja, permissões e aprovação.
3. **Provedor direto OpenAI**: usa a Responses API com chave de API para alguns caminhos alternativos. Esse fluxo possui contratos e riscos diferentes do login do Codex.

Misturar essas superfícies cria ambiguidades. O assistente operacional não deve editar a cópia instalada como se fosse um repositório de desenvolvimento, e o agente de desenvolvimento não deve herdar permissões de negócio sem um contrato explícito.

## Pontos fortes encontrados

### 1. Integração apropriada com o Codex

O uso do SDK Python do Codex e do app-server é adequado para conversas persistentes, streaming, cancelamento, planos, anexos, uso de tokens e gerenciamento de tarefas. A arquitetura não depende apenas de chamadas simples de texto.

### 2. Controle de contexto e escopo no servidor

Empresa, loja, usuário, sessão e permissões são tratados pelo backend. A autorização não depende apenas de instruções no prompt. Esse é um dos melhores aspectos da implementação.

### 3. Context Hub com governança

O Context Hub possui geração ativa, publicação manual, rollback, bloqueios de segurança, DLP e busca FTS5/BM25. No runtime instalado analisado ele estava ativo, com 517 documentos e 2.374 trechos indexados. Embeddings estão desativados, o que é uma escolha prudente enquanto não houver uma avaliação que prove ganho.

### 4. Política de ações sensíveis

Existem barreiras para ações mutáveis, pedidos de aprovação, auditoria e regras específicas. A aprovação pelo WhatsApp é negada, conforme a política do produto.

### 5. Seleção prévia de dados

Antes de enviar uma tarefa ao modelo, o sistema pode selecionar fontes e ferramentas relevantes. Essa estratégia reduz contexto irrelevante e cria uma base melhor para respostas fundamentadas.

### 6. Boa base de testes determinísticos

Os testes cobrem isolamento por empresa e loja, permissões de ferramentas, identidade de conversa, Context Hub, governança, recuperação de contexto, MCP e avaliação de respostas do Black Jhon.

## Achados prioritários

### P0 condicional / P1 alto — Verificação TLS desativada na Responses API

**Evidência:** backend/services/ia_providers.py, chamada à OpenAI próxima das linhas 1401–1416, usa verify=False.

**Risco:** se o provedor direto for ativado, token e conteúdo podem ficar expostos a interceptação; também elimina uma garantia básica de autenticidade do endpoint.

**Recomendação:** remover verify=False, preferir o SDK oficial e o armazenamento de certificados do sistema, testar em todos os ambientes e impedir o uso do provedor direto enquanto a validação TLS não estiver correta.

### P1 — Separar formalmente o Codex operacional do Codex de desenvolvimento

**Problema:** a cópia instalada em AppData é materializada pelo instalador e não é o checkout Git. Trabalhar nela como ambiente de desenvolvimento pode gerar alterações que desaparecem na atualização ou divergem do código-fonte.

**Recomendação:** definir dois contratos explícitos:

- assistente operacional: leitura e ações de negócio tipadas, com escopo de empresa/loja e aprovações;
- agente de desenvolvimento: somente checkout Git ou worktree isolado, com testes e revisão antes de aplicar alterações.

### P1 — O bloqueio de escopo ainda é parcialmente reativo

**Evidência:** backend/services/codex_console.py verifica alterações fora do escopo depois da execução.

**Risco:** detectar uma escrita indevida não desfaz automaticamente o efeito.

**Recomendação:** executar tarefas mutáveis em worktree ou diretório temporário, restringir raízes graváveis, validar o diff e só então aplicar mudanças aprovadas ao checkout principal.

### P1 — Confirmação de ações pode aceitar resultados incompletos

**Evidência:** backend/services/codex_agent_runtime.py considera confirmado qualquer dicionário não vazio; backend/services/codex_actions.py usa essa confirmação para concluir ações.

**Risco:** respostas com estado de fila, erro parcial ou dados incompletos podem ser tratadas como sucesso.

**Recomendação:** cada executor deve possuir uma pós-condição tipada e um comprovante de sucesso. A confirmação deve exigir estado final esperado, identificador da operação e ausência de erro.

### P1 — Exceção de permissão no Context Hub do WhatsApp

**Evidência:** documentos podem exigir permissão full, mas o coordenador do WhatsApp concede leitura ampla do Context Hub para sessões vinculadas; a recuperação não filtra cada trecho por required_permissions.

**Risco:** dentro da mesma empresa, uma sessão de menor privilégio pode receber conteúdo marcado como exclusivo para acesso completo.

**Recomendação:** aplicar ACL por documento/trecho na recuperação ou publicar um corpus específico para WhatsApp. Adicionar testes de matriz empresa × loja × canal × permissão.

### P1 — MCP nativo pronto, porém desativado

**Evidência:** existe backend/services/jk_codex_mcp_server.py com contexto assinado, allowlists, esquemas e prazos; o console força native_mcp=False e ainda usa marcações textuais para chamadas de ferramenta.

**Impacto:** o protocolo textual exige parsing próprio, aumenta ambiguidades e dificulta validação de argumentos.

**Recomendação:** ativar o MCP em canário, primeiro para ferramentas somente leitura. Autorizar servidor, plano, ferramenta e argumentos; manter o parser textual como fallback durante a migração e medir erros antes de removê-lo.

### P1 — Falta uma avaliação real para modelos, respostas e RAG

**Problema:** os testes atuais validam principalmente regras e roteamento. A avaliação embutida cobre poucos casos e não mede de forma suficiente factualidade, fontes, argumentos de ferramentas ou utilidade final.

**Recomendação:** criar um conjunto anonimizado de tarefas reais com resultado esperado e medir:

- correção da resposta e aderência às fontes;
- ferramenta escolhida e argumentos;
- isolamento e recusa correta;
- taxa de chamadas inválidas e ciclos de ferramenta;
- latência, tokens e custo;
- Recall@k e nDCG do Context Hub;
- comparação MCP nativo versus protocolo textual.

### P1 — Versões do SDK, CLI e protocolo precisam andar juntas

**Evidência:** requirements.txt fixa openai-codex 0.1.0b3 e openai-codex-cli-bin 0.137.0a4; há adaptação de protocolo privado para níveis de raciocínio.

**Risco:** a integração depende de componentes beta e de detalhes internos que podem mudar.

**Recomendação:** criar uma matriz suportada SDK × CLI × app-server, um teste de compatibilidade no início da aplicação e um processo único de atualização. Evitar chamadas a métodos privados quando houver API pública equivalente.

### P2 — O repositório não oferece instruções nativas ao Codex

**Evidência:** não há AGENTS.md e as pastas .codex e .agents estão vazias.

**Impacto:** regras importantes estão espalhadas pelo programa, memória operacional e prompts, mas não ficam disponíveis de forma consistente ao Codex de desenvolvimento.

**Recomendação:** adicionar um AGENTS.md curto com arquitetura, comandos de teste, regras de segurança, diferença entre checkout e runtime instalado e critérios de conclusão. Adicionar .codex/config.toml apenas para configurações compartilháveis e criar skills pequenas para fluxos repetitivos.

### P2 — Política de modelos ainda está centrada no GPT-5.5

**Evidência:** o catálogo já contém GPT-5.6 Sol, Terra e Luna, mas as cinco finalidades configuradas no runtime instalado usam codex:gpt-5.5.

**Recomendação:** não trocar tudo de uma vez. Executar avaliação sombra:

- Luna para seleção simples e tarefas de baixo custo;
- Terra para rotinas balanceadas;
- Sol para investigação e desenvolvimento complexo.

Preservar inicialmente o nível de raciocínio atual, comparar a qualidade e depois testar um nível inferior para reduzir custo e latência.

### P2 — Textos corrompidos podem degradar prompts e conhecimento

**Evidência:** há sequências de caracteres corrompidos em prompts de backend/services/ia_providers.py e em documentos incluídos no pacote de conhecimento.

**Impacto:** instruções ficam menos claras, prejudicam a recuperação e podem aparecer nas respostas.

**Recomendação:** normalizar tudo para UTF-8, adicionar detector de mojibake na integração contínua e testes de snapshot dos prompts principais.

### P2 — Telemetria de tokens e orçamento não cobre todos os agentes

**Problema:** o console registra uso em parte dos fluxos, mas coordenadores e agentes auxiliares não alimentam o mesmo livro-caixa.

**Recomendação:** registrar por empresa, tarefa, canal, agente, modelo e etapa; aplicar orçamento, limite por conversa e circuit breaker. Expor custo, latência, cache e taxa de sucesso em um painel único.

### P2 — Redação de dados sensíveis nos logs deve ser centralizada

**Problema:** mensagens e exceções arbitrárias podem ser persistidas e expostas por superfícies públicas.

**Recomendação:** aplicar uma função central de redação antes da persistência e antes da serialização pública, cobrindo tokens, segredos, dados pessoais e conteúdo bruto de ferramentas.

### P2 — Paridade de versão precisa ficar visível

**Evidência:** o checkout auditado está na versão 1.0.106; o manifesto e a geração ativa do Context Hub no runtime instalado indicavam 1.0.105. Os principais arquivos de Codex comparados tinham o mesmo hash.

**Recomendação:** mostrar separadamente versão do aplicativo, versão do pacote de conhecimento e geração ativa; bloquear diagnósticos comparativos quando a paridade não for conhecida.

### P3 — Dívida estrutural e recursos parcialmente mortos

**Achados:** codex_console.py e codex_assistant.py concentram milhares de linhas; algumas orientações são ignoradas após a seleção de dados; local_database_query é anunciado sem um adaptador geral; existe código posterior de aprovação móvel que fica inalcançável após a negação obrigatória do WhatsApp.

**Recomendação:** modularizar por responsabilidade, remover contratos não suportados e eliminar caminhos mortos sem alterar a política de que o WhatsApp nunca aprova ações.

## Roteiro recomendado

### Primeiros 7 dias

1. Corrigir ou bloquear o provedor direto com verify=False.
2. Corrigir a confirmação genérica de resultados mutáveis.
3. Aplicar ACL por trecho no Context Hub para WhatsApp.
4. Normalizar os prompts e documentos com mojibake.
5. Adicionar testes específicos para os quatro pontos acima.

### De 2 a 4 semanas

1. Criar o conjunto de avaliação real e estabelecer a linha de base do GPT-5.5.
2. Comparar GPT-5.6 Luna, Terra e Sol em execução sombra.
3. Adicionar AGENTS.md e configuração compartilhável do repositório.
4. Unificar tokens, custo, duração e orçamento de todos os agentes.
5. Tornar a paridade checkout/runtime/Context Hub visível no diagnóstico.

### De 1 a 3 meses

1. Ativar MCP nativo em canário para ferramentas somente leitura.
2. Migrar ações tipadas após validar segurança e observabilidade.
3. Isolar alterações de desenvolvimento em worktree e aplicar apenas diffs validados.
4. Modularizar os dois grandes serviços do Codex.
5. Avaliar busca híbrida ou embeddings somente se a métrica de recuperação demonstrar ganho.

## Critérios objetivos de sucesso

O programa poderá ser considerado próximo do uso ideal do Codex quando atingir:

- zero chamada à OpenAI com TLS não verificado;
- zero ação marcada como concluída sem pós-condição tipada;
- zero vazamento em testes de matriz de permissão do Context Hub;
- pelo menos 100 tarefas reais anonimizadas no conjunto de avaliação;
- 100% dos fluxos de agentes no mesmo livro-caixa de tokens e custos;
- queda mensurável de chamadas inválidas após o canário MCP;
- p95 de latência e custo definidos por classe de tarefa;
- versão do checkout, aplicativo instalado e Context Hub sempre identificáveis;
- regras de desenvolvimento documentadas em AGENTS.md;
- alterações mutáveis isoladas e revisadas antes de chegar ao checkout principal.

## Evidências da auditoria

### Runtime observado

- endpoint local /health respondeu com sucesso;
- Context Hub instalado: ativo, 517 documentos, 2.374 trechos, publicação manual, FTS5/BM25 e embeddings desativados;
- configuração instalada: cinco finalidades usando codex:gpt-5.5; raciocínio medium em perguntas e pós-venda;
- 241 tarefas históricas analisadas: 222 concluídas, 11 falhas, 5 canceladas, 2 aguardando entrada e 1 aguardando aprovação;
- 212 tarefas em sandbox read_only, 28 em full_access e 1 em workspace_write;
- 89 tarefas com protocolo mcp_v1, 11 com typed_catalog_text_v1 e 141 tarefas antigas sem protocolo registrado.

Esses números históricos misturam tipos de tarefa, versões e períodos diferentes. Eles servem como diagnóstico operacional, não como comparação causal entre modelos ou protocolos.

### Testes

- suíte dirigida do especialista: 192 testes aprovados;
- verificação independente do núcleo: 95 testes aprovados em 18,70 segundos;
- as suítes possuem sobreposição e não devem ser somadas como total único;
- não foi executada nesta auditoria uma avaliação online de qualidade com modelos reais.

## Fontes oficiais utilizadas

- OpenAI Codex best practices: https://learn.chatgpt.com/guides/best-practices
- AGENTS.md: https://learn.chatgpt.com/docs/agent-configuration/agents-md
- Configuração do Codex: https://learn.chatgpt.com/docs/config-file/config-basic
- MCP no Codex: https://learn.chatgpt.com/docs/extend/mcp
- Codex SDK para Python: https://learn.chatgpt.com/docs/codex-sdk#python-library
- Modelos GPT-5.6: https://developers.openai.com/api/docs/guides/latest-model
- Safety identifiers: https://developers.openai.com/api/docs/guides/safety-best-practices#implement-safety-identifiers

## Conclusão

O JK Sistema não está usando o Codex de forma improvisada. A fundação é boa, existe governança real e vários componentes avançados já foram construídos. O maior ganho agora virá de consolidar os contratos: segurança antes da execução, resultados comprovados, permissões aplicadas durante a recuperação, ferramentas nativas via MCP e avaliações que conectem qualidade, custo e latência.

Com as correções P1 e o roteiro de avaliação/modelos, a arquitetura pode evoluir de 7,5 para aproximadamente 9,0 sem uma reescrita geral. A recomendação é preservar o que já funciona e migrar de forma medida, começando pelos riscos de segurança e confirmação, depois MCP e GPT-5.6.
