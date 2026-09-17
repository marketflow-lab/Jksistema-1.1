# Validacao da versao 1.0.144

Data: 17 de setembro de 2026.

## Resultado local

A versao 1.0.144 foi preparada e validada no worktree dedicado `codex/release-v1.0.144`.

- Contrato imutavel do runtime aprovado.
- Perfis de release Electron, materializacao do runtime e inicializacao do backend aprovados.
- Dependencias controladas aprovadas: quatro lockfiles Node, 127 pacotes Python de runtime e sete pacotes Python de teste.
- Pacote-fonte do perfil `app-update` aprovado com o runtime `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.
- Regressao Python direcionada: 197 testes e 32 subtestes aprovados.
- Regressao de navegador de Perguntas e Solicitacoes aprovada.
- Compilacao dos 22 arquivos Python alterados aprovada.
- `git diff --check` aprovado.

A primeira execucao Python encontrou somente uma restricao de acesso do diretorio temporario global do Windows. O mesmo conjunto foi repetido com `--basetemp` dentro do worktree e passou integralmente.

## Publicacao automatizada

O workflow `Desktop release` executa novamente os contratos de release, gera o pacote leve, verifica o conteudo empacotado e publica os artefatos somente depois da conclusao de todos os gates.

O perfil desta versao e `app-update`. O runtime completo de base permanece identificado pelo contrato versionado e nao e reconstruido nesta atualizacao.

## Escopo operacional

Os testes e a publicacao usam o worktree e o runner do GitHub Actions. A copia instalada do aplicativo e seus dados locais nao sao alterados.
