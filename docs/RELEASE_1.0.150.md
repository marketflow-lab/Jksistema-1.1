# JK Sistema 1.0.150

- Consultas de lojas usam o ultimo estado consistente e autorizado sem aguardar travas de manutencao.
- Sincronizacao e Context Hub liberam as travas antes de processar e compactar os dados capturados.
- Automacao isola falhas por cliente e loja, preserva a ultima checagem valida e repete indisponibilidades temporarias.
- Renovacao de credenciais usa coordenacao por loja e provedor, com revalidacao antes de persistir tokens ou executar escritas externas.
- Conexoes antigas do Bling recebem identidade local durante a preparacao da sincronizacao, preservando credenciais e rollback.
- Consultas de IA e WhatsApp preservam o escopo autorizado; uma loja indisponivel nao e substituida por outra.
- A preferencia de aprovacao das respostas permanece editavel e integrada a esta base.
- Diagnosticos identificam esperas e retencoes de travas sem registrar credenciais, nomes de lojas ou conteudo operacional.

[Validacao desta versao](https://github.com/marketflow-lab/Jksistema-1.1/blob/v1.0.150/docs/RELEASE_1.0.150_VALIDACAO.md).

Atualizacao pequena no perfil `app-update`, preservando o runtime existente.
