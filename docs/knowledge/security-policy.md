# Política de segurança do Context Hub

O Context Hub trabalha com allowlist de fontes. O inventário técnico lê código,
contratos e schemas; em dados do tenant, lê exclusivamente
`info/<client_id>/SKU/*.json` pelo adaptador de schema 2.

Antes de uma geração ficar ativa, a validação deve bloquear segredos, tokens,
chaves privadas, credenciais OAuth, dados de compradores e PII. O achado
registra somente código, severidade e arquivo; nunca registra o valor detectado.

O conteúdo de uma nota não concede permissões. Toda consulta deriva tenant e
identidade da sessão, aplica permissões fora do texto e retorna apenas a geração
ativa. Falhas de validação ou publicação preservam a geração anterior.

ContextVault, `context_hub`, `.obsidian`, bancos operacionais e configurações
de integração são permanentemente excluídos dos descobridores e do Shared Sync.
