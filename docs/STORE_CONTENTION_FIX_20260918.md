# Correcao da contencao de lojas

Base: `61835d9` (1.0.148). Implementacao em branch dedicada; integracao e publicacao dependem de autorizacao posterior.

## Comportamento

- `ler_lojas` consulta a projecao publicada e resolve credenciais atuais pelo `store_id`. Nao adquire as travas de lojas, cadastro, custos ou fotos.
- `carregar_lojas` conserva recuperacao, migracao e manutencao. Caminhos de commit continuam consultando o estado canonico dentro da transacao.
- Identidades de Mercado Livre e Bling sao independentes. Dados ausentes ou ainda nao publicados retornam indisponibilidade temporaria, sem autorizar credenciais de uma conexao diferente.
- Sessao central e associacao local do Turbo continuam limitando o acesso. Projecao publica nao contem tokens nem fingerprints privados de conexao.
- SharedSync captura bytes consistentes sob as travas. Hashes, transformacao de deltas e ZIP usam exclusivamente a captura depois da liberacao.
- Context Hub captura as fontes, processa fora das travas e revalida identidade e versao antes de ativar a geracao. Geracao anterior e `80_Curadoria` permanecem preservadas em conflito.
- OAuth usa coordenacao especifica por cliente, loja e provedor; chamadas de rede ficam fora da trava geral. A persistencia compara conexao e credencial de origem antes de gravar.
- Automacao preserva a referencia anterior em checagens incompletas, isola falhas e repete indisponibilidades em 5, 15 e 30 segundos; depois retoma o intervalo configurado. Pos-venda automatico permanece desativado.
- Diagnostico de esperas e retencoes usa fila limitada e um consumidor separado. Registra operacao, fase, recurso opaco, processo, duracao e resultado; nao registra nomes, caminhos ou dados operacionais. Falhas de instrumentacao nao impedem as operacoes.

## Compatibilidade e identidade

A projecao interna passa ao schema 2 para vincular separadamente cada provedor. O leitor aceita schema 1 e solicita atualizacao em segundo plano; Bling sem identidade publicada permanece temporariamente indisponivel. Campos internos de identidade nao sao incluidos nos cartoes publicos. Rotas e assinaturas publicas sao preservadas; a automacao acrescenta o indicador condicional de indisponibilidade para conservar a ultima checagem valida.

Consumidores de IA e WhatsApp propagam indisponibilidade e erros de autorizacao, sem recorrer a arquivos brutos ou ao cliente `default`. Uma selecao explicita de loja nunca se transforma em uma consulta a outras lojas. Transacoes de escrita continuam revalidando o estado canonico e chamadas externas revalidam a conta preparada antes de agir.

## Comparacao de espera

Teste sintetico com outro processo mantendo a trava por mais de 10 segundos:

| Caminho | Tempo observado | Resultado |
| --- | ---: | --- |
| Leitura legada sob a trava | aproximadamente 10.000 ms | Timeout |
| Leitura publicada, primeira consulta | 8,16 ms | Sucesso |
| Leitura publicada, consulta seguinte | 3,52 ms | Sucesso |

O limite de aquisicao da trava de lojas continua em 10 segundos. Os numeros acima medem leitura local, excluem APIs externas e nao constituem medicao do ambiente instalado.

Testes adicionais pausam hash, delta, ZIP e processamento do Context Hub; outro escritor adquire a trava em menos de 500 ms e a captura continua contendo os dados anteriores. Testes de alteracao de identidade/versao impedem publicar resultados preparados para uma conexao antiga.

## Verificacoes

As suites usam diretorios temporarios sinteticos. Grupos abaixo possuem alguma sobreposicao e nao devem ser somados como um unico total:

| Grupo | Resultado |
| --- | --- |
| SharedSync | 536 aprovados; 3 ignorados |
| Context Hub e publicacao | 36 aprovados |
| Consumidores, cadastro, importacao, estoque, NCM e IA | 288 aprovados |
| Leitura, OAuth, persistencia e transporte | 94 aprovados |
| Automacao, estado e contratos dos endpoints | 47 aprovados |
| Orquestrador de respostas, envio confirmado e contratos | 199 aprovados |
| Coordenacao e diagnostico | 17 aprovados; 1 ignorado |
| Contratos de vendas, inventario, IA e ciclo de respostas | 28 aprovados |

SharedSync e orquestrador tiveram fixtures antigas adaptadas ao novo leitor/contexto sintetico; os casos afetados foram reexecutados com sucesso. As contagens desses grupos consolidam a execucao ampla e a reexecucao direcionada. O grupo final de OAuth passou integralmente depois das correcoes de revisao.

Tambem passaram a guarda de arquitetura que impede reintroduzir leituras de manutencao e os scripts Node de envio manual, automacao e identidade de lojas. Compilacao dos Python alterados e `git diff --check` completam a verificacao.

O inventario de consultas, manutencao e validacoes de commit esta em `STORE_OPERATIONAL_READ_POLICY.md`. Casos novos incluem troca de conexao durante refresh, token rotacionado com edicao concorrente de metadados, revalidacao antes do retry HTTP 401, indisponibilidade apos buscar perguntas e ausencia de desvio para outra loja.

Testes de links simbolicos podem ser ignorados no Windows quando a conta nao possui a permissao correspondente. Isso nao ignora os testes de mutex entre processos.

## Commits por area

- `4a841a7`: captura e publicacao do Context Hub.
- `287806f`: captura e processamento do SharedSync.
- `6465b17`: coordenacao e diagnostico.
- `4861f46`: isolamento de falhas e estado da automacao.
- `7524881`: consumidores e propagacao de indisponibilidade.
- `dc7f017`: leitura autorizada e projecao por provedor.
- `cbe18a2`: renovacao de credenciais e revalidacao de escritas.

O commit final de validacao acrescenta a documentacao, o limite de 10 segundos do gate OAuth e o tratamento de renovacao ocupada na automacao. Nenhuma alteracao de versao, integracao no checkout principal, publicacao ou atualizacao instalada integra esta entrega.
