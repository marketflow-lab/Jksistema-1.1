# Validação da versão 1.0.141

Base do aplicativo: `b9bcec3083b961f5bc8ee916155c216e4e90d170`, com as alterações já integradas para a próxima versão. A preparação desta release altera metadados, manifesto editorial, documentação e testes; não altera a lógica do aplicativo em relação à base.

## Pacote

- Instalador NSIS gerado com publicação automática desabilitada.
- Verificação dos recursos antes e depois do empacotamento aprovada.
- Instalação offline em diretório temporário: runtime Python 3.14.6, dependências, importação do backend e reutilização idempotente aprovados.
- Simulação de atualização empacotada de 1.0.99 para 1.0.141 aprovada.
- SHA-512 do instalador coincide com `latest.yml`.
- Nenhuma instalação ou atualização do aplicativo de uso diário foi executada.

| Artefato | Bytes | SHA-256 |
|---|---:|---|
| JK-Sistema-Cliente-Setup-1.0.141.exe | 943129639 | bf5fda2b7c5bf54b10531dd7c36609886881677ec479fb2f8e359785a0cc5798 |
| JK-Sistema-Cliente-Setup-1.0.141.exe.blockmap | 977068 | 4ce659a75e888583bab6b2f5e6ad583a2bf4f61ef9de4f44264b07c01231bbf9 |
| latest.yml | 371 | df434fa9791a080fc4c01fe6b24b06fde4d92f7393a922c7100300d9c19788b3 |

## Testes e reexecuções direcionadas

A execução Python completa terminou com **5.453 aprovados, 42 falhas, 26 ignorados e 85 subtestes aprovados**. As 42 falhas foram investigadas e reexecutadas por grupo; não foi executada outra suíte completa nem se declara a suíte original integralmente aprovada.

| Grupo das falhas originais | Casos | Resultado da investigação e reexecução |
|---|---:|---|
| Diretório de trabalho do WhatsApp bloqueado pelo sandbox | 8 | Aprovados com LOCALAPPDATA temporário isolado. |
| Identidades dependentes do armazenamento seguro | 13 | Aprovados com chave HMAC sintética estável em memória; permanece a limitação descrita abaixo. |
| Contratos antigos de IA | 15 | Testes alinhados às mudanças aprovadas de preservação literal, assinatura orientada no prompt, contexto integral e inicialização das fixtures; aprovados. |
| Versão fixa | 2 | Expectativas atualizadas para 1.0.141; aprovados. |
| TLS | 2 | Aprovados em ambiente sem substituição do bundle de certificados; a execução inicial recebia um caminho de CA válido, em vez do booleano esperado pela fixture. |
| Estabilidade da escrita de ficha | 1 | Aprovado isoladamente; teste concorrente sensível a agendamento. |
| Contagem global de rotas | 1 | Sete rotas conferidas nos commits e expectativa atualizada; hash OpenAPI de Vendas preservado; aprovado. |

Os arquivos alterados de IA também tiveram verificação ampliada: 237 casos do lote de contexto/voz/limites e 33 de compatibilidade/replay. Três casos do primeiro lote precisam do bootstrap `backend_api`, já presente na suíte completa. As rotas e os contratos de versão tiveram 13 casos aprovados.

Node/Electron: 128 de 138 arquivos passaram na execução inicial; nove arquivos passaram após corrigir fixtures antigas, disponibilizar o pacote e permitir subprocessos isolados. **137 de 138 arquivos verificados**; permanece a pendência estrutural abaixo. As expectativas de foco e timeout continuam verificadas.

Também aprovados: 70 testes e compilação TypeScript do gateway; cinco sondas sem chamadas externas; contrato seguro do Bug Hunter (um aprovado e um cenário de mutação ignorado); locks de dependências; 24 espelhos HTML; manifesto editorial; compilação dos Python alterados e `git diff --check`.

## Pendências preexistentes

1. `tests/cadastro_frontend_architecture.js`: `static/cadastro/main/06-importacoes-catalogos.js` tem 440 linhas, acima do orçamento de 400. A expansão pertence ao commit integrado `26b58e8`. O limite não foi ampliado para ocultar a falha; não houve falha funcional demonstrada nesse módulo.
2. Quando o armazenamento seguro do Windows não está disponível, `_resolve_hmac_key()` gera uma chave efêmera diferente por chamada. Isso pode perder a continuidade das identidades do WhatsApp. O comportamento já existe na versão 1.0.140 (commit ancestral `0258ff9`), e os arquivos envolvidos não mudaram nesta release. Os 13 casos passaram com armazenamento estável simulado; isso não comprova nem corrige o fallback sem armazenamento seguro.

Essas pendências não foram corrigidas durante o empacotamento e não devem ser descritas como testes aprovados no ambiente original.
