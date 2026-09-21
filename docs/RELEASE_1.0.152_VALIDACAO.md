# Validacao da versao 1.0.152

Data: 21 de setembro de 2026.

## Base e escopo

Release preparada sobre a `v1.0.151` publicada, incorporando a alteracao aprovada que preserva respostas geradas pela IA e suas edicoes durante atualizacoes assincronas, troca de pergunta e recarregamento da pagina.

O perfil permanece `app-update`. Dependencias, runtime Python e contrato do instalador nao mudaram. Runtime preservado: `d20e840199916c287a69f26e0ac478b16f6d98fcb704c48883c2ed6bd4cd601b`.

## Verificacoes locais

- Contratos de persistencia, envio manual e automacao passaram em quatro regressoes Node; a recuperacao no navegador foi aprovada no Chromium.
- 111 testes Python direcionados ao salvamento, recuperacao, isolamento por tenant, loja e pergunta, expiracao, autorizacao, carregamento, envio e conflitos otimistas foram aprovados.
- 6 testes de distribuicao e alinhamento de versao foram aprovados.
- Locks de 4 dependencias Node, 127 pacotes Python de runtime e 7 pacotes Python de teste foram conferidos.
- Os 24 espelhos HTML estaticos foram conferidos sem alteracoes.
- Gates de perfis de release, materializacao do runtime, bootstrap do backend, contrato do runtime e pacote `app-update` foram aprovados.
- Compilacao dos Python alterados, sintaxe dos JavaScript alterados e `git diff --check` foram aprovados.
- O limite arquitetural anterior de 120 linhas em `manual_questions.py:ml_perguntas_responder_manual`, presente sem alteracao na `v1.0.151`, permanece registrado separadamente.

## Publicacao

O workflow `desktop-release.yml` deve executar com `release_tag=v1.0.152`, `release_mode=app-update` e `publish=true`.

O workflow valida o pacote, gera `SHA256SUMS.txt` e publica os artefatos a partir de um draft completo. A copia instalada nao participa deste procedimento.
