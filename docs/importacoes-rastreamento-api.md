# Rastreamento de embarques e navios

## Dados basicos

Para rastrear a carga no portal do armador:

- armador;
- tipo da referencia;
- Booking, BL ou numero do container.

Para consultar a posicao AIS do navio pela API:

- IMO com 7 numeros e digito verificador valido; ou
- MMSI com 9 numeros.

Nome do navio, viagem, POL, POD, ETD e ETA sao dados de conferencia e exibicao.
Eles nao substituem IMO/MMSI na identificacao tecnica do navio.

## Balao de rastreamento da carga

Ao clicar em **Rastrear carga**, a tela mostra primeiro os dados cadastrados no
embarque e, para referencias COSCO, consulta a pagina publica oficial em segundo
plano. O retorno disponivel e exibido no proprio balao: situacao do Booking e do
BL, ultimo status, origem, destino, equipamento, ETD, ETA e disponibilidade da
carga no destino.

Essa consulta publica da COSCO nao exige chave, credito ou periodo de teste. Ela
roda somente no aplicativo Electron, em uma sessao de navegador isolada, sem
reutilizar a sessao do Mercado Livre e sem gravar o resultado no cadastro. Como
a pagina pertence ao armador, mudancas de layout, indisponibilidade ou desafios
de acesso podem impedir a leitura automatica. Nesses casos o balao informa a
falha e mantem o botao **Abrir portal oficial**.

Quando uma referencia cadastrada como BL nao e encontrada, o leitor reconhece a
resposta negativa do portal e tenta a mesma referencia como Booking. Se essa
segunda consulta encontrar a carga, o balao informa o tipo usado e o botao do
portal passa a abrir a consulta valida. O cadastro original nao e alterado
automaticamente.

Para outros armadores, o balao preserva os dados cadastrados e oferece o portal
oficial, sem afirmar que houve consulta automatica.

## Configuracao da API

O backend usa o endpoint `GET /api/v0/vessel` da Datalastic e envia a credencial
somente pelo header `x-api-key`. Configure no `.env` carregado pelo backend:

```dotenv
JK_DATALASTIC_API_KEY=sua_chave_aqui
```

Opcoes:

```dotenv
JK_DATALASTIC_TIMEOUT_SECONDS=8
JK_DATALASTIC_CACHE_TTL_SECONDS=300
```

- O timeout aceito pelo sistema fica entre 2 e 30 segundos.
- O cache aceito fica entre 30 e 3600 segundos e reduz consultas repetidas e
  consumo de creditos.
- A chave nao deve ser colocada no HTML, localStorage ou repositorio.

Sem uma chave valida, o endpoint local responde com erro controlado e a tela
informa que a API ainda nao esta configurada.

## Endpoint interno

```text
GET /api/importacoes/rastreamento/navio?imo=9996678
GET /api/importacoes/rastreamento/navio?mmsi=249369000
```

No backend real, a rota reutiliza a dependencia de autenticacao do JK Sistema.
A resposta normalizada contem identificadores, coordenadas, velocidade, curso,
estado de navegacao, horario do ultimo sinal, destino e ETA informados pelo AIS.
