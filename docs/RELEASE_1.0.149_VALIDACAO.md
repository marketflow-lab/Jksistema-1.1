# Validacao da versao 1.0.149

Data: 18 de setembro de 2026.

## Base e escopo

Release preparada sobre `v1.0.148`, incorporando a correcao aprovada da caixa de aprovacao de Perguntas.
A release 1.0.148 publicada durante a preparacao foi adotada como nova base para preservar a correcao de concorrencia das lojas.

## Verificacoes locais

- A regressao funcional afetada aprovou 233 testes Python.
- Os contratos e a arquitetura aprovaram 68 testes e 32 subtestes; o unico desvio e o limite de 120 linhas em `manual_questions.py`, ja presente e reproduzido na release 1.0.148.
- Os dois testes JavaScript do frontend afetado foram aprovados.
- Os 24 espelhos HTML foram conferidos depois de sincronizar a copia de compatibilidade de Perguntas com a fonte estatica canonica.
- O estado da caixa de aprovacao foi verificado no frontend, no contrato da API e na persistencia por loja.
- O envio direto foi exercitado com resposta validada, evidencia suficiente e job atual.
- Os bloqueios de envio foram exercitados para aprovacao marcada, evidencia insuficiente e revisao humana obrigatoria.
- A consulta remota antes do envio e o bloqueio para pergunta ja respondida foram preservados.
- Os contratos de versao, dependencias, runtime e os tres gates do workflow de desktop foram aprovados para a versao canonica 1.0.149.
- O pacote leve foi construido com 1.055 arquivos gerenciados no perfil `app-update`, sem bytecode ou caches indevidos, e preservou o identificador imutavel do runtime `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Artefatos locais de validacao

| Arquivo | Tamanho | SHA-256 |
| --- | ---: | --- |
| `JK-Sistema-Cliente-Update-1.0.149.exe` | 92.112.945 bytes | `0ae205bbd9ed383a9429458ea2479dac8b98504ae363159ab8fd3ef010f3bc03` |
| `JK-Sistema-Cliente-Update-1.0.149.exe.blockmap` | 97.169 bytes | `b4ec949492e006d499d355deb2edeb4a5b8af97bb0563a69e3c90fb4aa3e9bf3` |
| `latest.yml` | 372 bytes | `7dbc07074e332c565fef31cb70e579ca8bceb56ab7c99a0b76879b111277aa21` |

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.149`, `release_mode=app-update` e `publish=true`.
O workflow verifica o pacote antes e depois da construcao, gera `SHA256SUMS.txt` e publica os artefatos a partir de um draft completo.
Os resultados remotos e os hashes oficiais ficam no workflow e na release.

Nenhuma atualizacao da copia instalada e executada por este procedimento de publicacao.
