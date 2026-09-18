# JK Sistema 1.0.147

- A geracao de sugestoes para Perguntas e pos-venda voltou a se comunicar com o runtime Codex atual.
- O pacote leve agora inclui a versao compativel do SDK Python usada pelo provedor de respostas, mantendo o runtime principal da instalacao.
- Uma falha interna de comunicacao encerra a sessao defeituosa e faz uma unica tentativa em uma sessao nova.
- O identificador da conversa passa a ser salvo somente depois que a chamada termina com sucesso, evitando que um retry reutilize uma sessao quebrada.
- A execucao do provedor continua limitada ao sandbox de somente leitura e sem aprovacao de comandos.

[Validacao desta versao](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.147/docs/RELEASE_1.0.147_VALIDACAO.md).

Esta versao e uma atualizacao pequena no perfil `app-update`; ela preserva o runtime local e nao substitui o instalador completo usado em instalacoes novas.
