# JK Sistema 1.0.148

- Geracao de sugestoes e envio manual consultam a configuracao publicada das lojas sem aguardar a manutencao do catalogo.
- A IA aguarda indisponibilidades temporarias das lojas com limite de tentativas, preservando a sessao e as verificacoes de identidade.
- Falhas locais posteriores a um envio confirmado pelo Mercado Livre exibem um aviso de registro pendente, sem indicar que a resposta precisa ser reenviada.
- O envio preserva a identidade exata da loja e nao repete automaticamente a publicacao.
- Contas cujo site ainda nao esta salvo localmente continuam usando a identidade validada na consulta.

[Validacao desta versao](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.148/docs/RELEASE_1.0.148_VALIDACAO.md).

Atualizacao pequena no perfil `app-update`, preservando o runtime existente.
