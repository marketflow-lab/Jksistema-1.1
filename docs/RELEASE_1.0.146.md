# JK Sistema 1.0.146

- A geracao de respostas com IA agora distingue falhas de autenticacao local, indisponibilidade do runtime, erros transitorios do provedor e expiracao real do contexto.
- Falhas genericas do provedor deixaram de invalidar o contexto validado ou encerrar a sessao do JK Sistema.
- O texto digitado pelo operador permanece preservado depois de uma falha, e o botao **Gerar IA** volta a ficar disponivel quando o job termina.
- Falhas transitorias respeitam a politica de retry e o deadline; falhas de autenticacao e configuracao encerram imediatamente com mensagens especificas.
- Os cards das lojas no cabecalho de Perguntas e pos-venda agora acompanham a largura do nome e quebram automaticamente para a linha seguinte.
- Os indicadores de conexao, contadores e avisos de reconexao permanecem visiveis sem criar rolagem horizontal em telas estreitas.

[Validacao desta versao](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.146/docs/RELEASE_1.0.146_VALIDACAO.md).

Esta versao e uma atualizacao pequena no perfil `app-update`; ela preserva o runtime local e nao substitui o instalador completo usado em instalacoes novas.
