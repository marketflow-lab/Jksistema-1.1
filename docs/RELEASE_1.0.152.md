# JK Sistema 1.0.152

- Respostas geradas pela IA permanecem no compositor durante atualizacoes assincronas do painel de Perguntas.
- A resposta e as edicoes do operador voltam apos trocar de pergunta ou recarregar a pagina na mesma sessao.
- O navegador guarda somente os identificadores do job; o texto do rascunho permanece selado no backend por ate sete dias.
- O salvamento automatico usa versao e hash da proposta para impedir que uma resposta antiga sobrescreva uma edicao mais recente.
- Envio, cancelamento, logout, troca de tenant e revogacao da loja removem somente a referencia correspondente.

[Validacao desta versao](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.152/docs/RELEASE_1.0.152_VALIDACAO.md).

Atualizacao pequena no perfil `app-update`, preservando o runtime existente.
