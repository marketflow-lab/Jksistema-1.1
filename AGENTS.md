# JK Sistema - orientacoes para agentes

## Ambientes

- **Codex Desktop/CLI** e a ferramenta de desenvolvimento. Toda alteracao de codigo deve nascer em um Git Worktree dedicado.
- **BlackJohn (Joao Pretinho)** e o assistente interno do JK Sistema. Ele pode consultar dados e preparar operacoes tipadas, mas nao pode editar codigo, abrir shell ou elevar o proprio sandbox.
- O checkout principal, a copia instalada em `%APPDATA%\JK Sistema Cliente\local_app` e dados em `info/<cliente>` nao devem ser editados como codigo-fonte.

## Fluxo de desenvolvimento

1. Confirme que o Worktree nasceu do commit correto e esta limpo.
2. Obtenha a primeira aprovacao antes da execucao que alterara o Worktree. Quando a configuracao do projeto confiavel estiver ativa, o perfil versionado inicia em sandbox somente leitura e encaminha a liberacao de escrita ao usuario. Overrides de maior precedencia nao suspendem esta regra processual.
3. Preserve alteracoes locais do usuario; um arquivo sujo nao aparece automaticamente no Worktree. Se a tarefa depender dele, pare e resolva a dependencia explicitamente.
4. Implemente e teste no Worktree.
5. Apresente o diff e obtenha uma segunda aprovacao explicita antes de qualquer Handoff para o checkout local.
6. Nao faca push, tag, release, instalador, deploy ou atualizacao da copia instalada sem pedido explicito.

## Seguranca e dados

- Separe sempre cliente, usuario, loja, conta, seller e site.
- WhatsApp e canal de consulta e contexto; nunca e canal de aprovacao de mutacoes.
- Nao registre prompts, respostas, historico, telefone, e-mail, CPF/CNPJ, endereco, credenciais, argumentos/resultados de ferramentas, saida de terminal ou caminhos absolutos.
- Dados operacionais atuais permanecem nas APIs, bancos e arquivos estruturados canonicos. Obsidian e Context Hub sao camadas editoriais/versionadas.
- Ferramentas externas devem ser allowlisted, somente leitura quando declaradas assim, idempotentes e auditaveis.

## Compatibilidade e qualidade

- Preserve rotas, imports e schemas publicos durante extracoes; use fachadas ou adaptadores de compatibilidade.
- Antes de alterar comportamento, congele ou atualize conscientemente os hashes de contratos.
- Rode primeiro testes direcionados, depois a suite proporcional ao risco, `git diff --check` e compilacao dos Python alterados.
- Mudancas em IA exigem casos sem evidencia, fontes conflitantes, prompt injection, isolamento de tenant/loja, timeout e fallback.
- Mudancas em Context Hub exigem geracoes isoladas, publicacao atomica, rollback e preservacao de `80_Curadoria`.
- Mudancas em MCP exigem schema fechado, plano assinado, argumentos materializados e exclusividade de protocolo por tentativa.

## Tipo de tarefa

- **Diagnosticar:** provar a causa sem implementar.
- **Planejar:** produzir especificacao decisoria sem editar.
- **Implementar:** editar no Worktree, testar e entregar o diff.
- **Publicar:** somente quando o usuario pedir explicitamente.
