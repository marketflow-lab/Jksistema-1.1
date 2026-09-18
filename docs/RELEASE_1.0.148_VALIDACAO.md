# Validacao da versao 1.0.148

Data: 18 de setembro de 2026.

## Base e escopo

Release preparada sobre `v1.0.147`, incorporando a correcao aprovada `c8c6148`.
Nenhuma outra alteracao pendente foi incorporada sem aprovacao.

## Verificacoes locais

- 361 testes Python aprovados: orquestrador, preflight, envio manual, contrato de endpoints, fila, configuracao e snapshot de lojas, contexto de catalogo, distribuicao e alinhamento de versao.
- Casos incluem bloqueio concorrente de lojas, limites de espera, isolamento de tenant/loja/seller/site, ausencia e conflito de evidencia, prompt injection e falhas locais apos confirmacao remota.
- Contrato frontend persistente de Perguntas aprovado; tres cenarios de envio manual aprovados, incluindo falha de renderizacao apos confirmacao.
- Gates de perfis de release, materializacao do runtime e inicializacao do backend aprovados.
- Contratos de dependencias e runtime aprovados; runtime_id preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.
- Compilacao dos Python alterados, sintaxe JavaScript e `git diff --check` aprovados.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.148`, `release_mode=app-update` e `publish=true`.
O workflow verifica o pacote antes e depois da construcao, gera `SHA256SUMS.txt` e publica os artefatos a partir de um draft completo.
Os resultados remotos e os hashes oficiais ficam no workflow e na release.

Nenhuma atualizacao da copia instalada e executada por este procedimento de publicacao.
