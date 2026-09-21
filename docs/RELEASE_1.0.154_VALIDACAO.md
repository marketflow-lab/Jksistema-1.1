# Validacao da versao 1.0.154

Data: 21 de setembro de 2026.

## Base e escopo

Release preparada sobre a `v1.0.153` publicada, incorporando a correcao aprovada que gera um `Setup` completo em todas as versoes e mantem o `Update` leve para instalacoes existentes.

O perfil desta publicacao e `runtime-update`. O contrato de runtime permanece `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b` e o instalador transporta o Microsoft Visual C++ Redistributable, Python 3.14.6, 127 wheels e Whisper small.

## Verificacoes locais

- Os testes dos perfis de release e da preparacao dos artefatos foram aprovados em Windows PowerShell e PowerShell 7.
- Os contratos de dependencias, runtime, materializacao e bootstrap do backend foram aprovados.
- O instalador completo foi gerado e conferido com verificacao profunda do runtime offline.
- O pacote confirmou a presenca e o hash esperado do Microsoft Visual C++ Redistributable.
- O manifesto `latest.yml`, os nomes dos artefatos e os checksums foram conferidos para o perfil `runtime-update`.
- `git diff --check` foi aprovado.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.154`, `release_mode=runtime-update` e `publish=true`.

O workflow valida o pacote, gera `SHA256SUMS.txt` e publica o `Setup` completo a partir de um draft. A copia instalada nao participa deste procedimento.
