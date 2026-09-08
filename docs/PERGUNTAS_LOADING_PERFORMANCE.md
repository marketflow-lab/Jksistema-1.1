# Carregamento de perguntas: validação e medição

## Método

Teste local de integração com o HTML e os scripts reais, executado no Chrome headless com Playwright. Um servidor HTTP local fornece os assets; todas as APIs são interceptadas e usam somente dados sintéticos. Nenhuma consulta ou resposta é enviada ao Mercado Livre.

O modo `--baseline` lê os assets do commit `52c4b89` por `git show`, sem trocar ou editar o checkout. O modo padrão usa os assets do worktree. Ambos pré-carregam os assets antes da navegação para excluir o custo da extração do Git e das leituras de arquivos.

A fixture contém 11 lojas com 60 perguntas por loja: dez consultas bem-sucedidas, uma delas com atraso de 900 ms, e uma consulta com erro 503; as demais consultas de lista levam 35 ms. Complementos de anúncio levam 50 ms e detalhe leva 75 ms. Um `MutationObserver` mede o instante da primeira lista renderizada desde a inicialização da página. A fixture não reproduz a latência ou o processamento interno do Mercado Livre.

## Resultado observado em 8 de setembro de 2026

| Medição | Base `52c4b89` | Implementação |
| --- | ---: | ---: |
| Primeira lista com perguntas | 1.189 ms | 476 ms |
| Chamadas iniciais de lista/contadores | 22 | 12 |
| Voltar da página 2 para a página 1 | 939 ms | 99 ms |
| Consultas de blocos ao voltar | 11 | 0 |

Na base, as 22 chamadas são 11 consultas completas de perguntas e 11 consultas completas usadas como contadores. Na implementação, são 11 consultas leves e uma consulta de resumo. Chamadas de enriquecimento não estão incluídas nessa contagem. A coluna da implementação usa a execução final da suíte expandida. Durante o desenvolvimento, execuções anteriores mediram de 212 a 439 ms para a primeira lista e de 22 a 109 ms para voltar; tempos absolutos variam com a execução do navegador e com os ajustes entre versões.

Estes números demonstram o comportamento no cenário controlado, não um ganho medido na operação real. Não foram medidos custo, tempo dos servidores do Mercado Livre, volume de consultas internas remotas ou performance com dados de clientes.

## Cenários verificados

- A primeira lista aparece antes da loja lenta, mesmo com uma loja indisponível.
- A interface usa `store_id` canônico e deixa de consultar a rota pesada.
- O paginador combina quatro páginas de 20 registros em ordem global, sem duplicatas, com recarga do buffer de uma loja enquanto as outras ainda têm dados; não consulta o mesmo bloco duas vezes e limita a quatro consultas simultâneas.
- A página anterior reaparece em até 200 ms e reutiliza os blocos já consultados.
- Trocas rápidas de loja descartam resultados anteriores, inclusive após a conclusão da consulta lenta.
- Atualização durante digitação preserva rascunho e foco.
- Erro 503 preserva dados autorizados e rascunho; erro 403 remove a lista em cache.
- Uma lista capturada antes de um envio manual fica retida na fixture; após a resposta ser confirmada, a lista antiga é liberada e não restaura a pergunta como pendente.
- O evento de logout remove lista e detalhe.
- Duas lojas homônimas mantêm contadores distintos, tanto no estado quanto nos cartões; responder na segunda invalida a revisão dessa loja e impede que sua consulta antiga restaure a pendência.
- Trocar o token no mesmo documento durante um GET pendente remove imediatamente lista, detalhe e contadores ao receber a resposta. Carregar novamente as lojas funciona com a nova sessão.
- Com o relógio controlado, dados de 590 segundos permitem fallback preservando o horário original; aos 610 segundos a lista expirada não reaparece, mesmo após outra falha remota.
- O navegador termina sem erros JavaScript.

Os testes de backend cobrem autorização, separação de escopos, caches, enriquecimento, invalidação e orçamento de leitura. A fixture de navegador complementa esses testes; não substitui uma validação integrada com autenticação e APIs reais.

## Validação integrada

Na validação final, 170 testes Python passaram, cobrindo os novos endpoints,
transporte, cache, contratos, automação, respostas públicas, pós-venda,
identidade de loja, contas centrais e provisionamento do runtime. As cinco
regressões JavaScript de indicadores, automação, pesquisa persistente,
pós-venda manual e treinamento por loja também passaram.

O teste de treinamento por loja no navegador passou com os novos assets.
Os 15 arquivos Python alterados foram compilados, os scripts JavaScript
alterados passaram na checagem de sintaxe e os HTML canônico e espelhado
permanecem idênticos. A verificação de whitespace não encontrou erros.

As alterações estão organizadas em consultas/cache, interface/paginação e
integração/validação. A base de comparação preserva as alterações de
desenvolvimento já existentes e incorpora os ajustes de runtime e testes
da versão estável publicada; o trabalho de desempenho não altera a versão
do aplicativo nem gera artefatos de instalação.

## Reproduzir os testes de navegador

Disponibilize o pacote `playwright` pelo ambiente Node ou por `NODE_PATH`. O teste usa o canal Chrome instalado; `PLAYWRIGHT_CHROMIUM_EXECUTABLE` permite informar outro executável Chromium.

```powershell
node tests/perguntas_loading_browser.js --baseline
node tests/perguntas_loading_browser.js
```

Cada execução imprime somente métricas sintéticas e o resultado das asserções. Para validação operacional posterior, repetir abertura, troca de loja e paginação com cache frio e preenchido, registrando apenas durações e quantidades de chamadas, sem conteúdo de perguntas, credenciais ou identificadores de clientes.
