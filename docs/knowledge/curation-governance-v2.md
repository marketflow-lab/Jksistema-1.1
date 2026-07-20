# Governanca da curadoria no Context Hub (schema 2)

O Obsidian e o editor humano do diretorio `80_Curadoria`. Uma nota Markdown
nao se torna conhecimento ativo por declarar `status`, `ai_usage`,
`truth_class`, `tenant_scope` ou `authority` no frontmatter.

O fluxo efetivo e persistido no SQLite privado do tenant:

```text
draft -> reviewed -> approved -> ready -> active
             \-> rejected
```

- A validacao aplica contrato de frontmatter e DLP ao corpo e aos metadados.
- Revisao e aprovacao sao vinculadas ao SHA-256 do arquivo completo.
- Qualquer edicao feita no Obsidian invalida validacao, revisao e aprovacao,
  retornando a nota para `draft`.
- Na indexacao, o servidor deriva o tenant da sessao autenticada e forca
  `truth_class: human_curated`, `authority: advisory`, sensibilidade interna e
  permissao administrativa. Autoridade declarada dentro da nota e ignorada.
- Somente uma acao manual de full-admin cria/publica a geracao ativa. O watcher,
  quando habilitado, observa apenas `80_Curadoria` e cria no maximo uma geracao
  `ready`; ele nunca publica.
- A busca usa FTS5/BM25 somente sobre a geracao `active` do banco isolado do
  tenant. Embeddings permanecem desligados ate haver avaliacao de qualidade,
  privacidade, custo e estrategia de reindexacao.

Backups incluem somente os arquivos Markdown de `80_Curadoria`, sao
criptografados e autenticados, e nao armazenam a senha. A restauracao valida
caminhos, hashes, UTF-8, frontmatter e DLP em uma area de staging antes da
troca. Senha incorreta ou arquivo adulterado falha antes de alterar a
curadoria. Uma restauracao invalida aprovacoes e nao muda a geracao ativa.
