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

Quando o retorno publico inclui portos e horarios, o balao tambem desenha uma
linha do tempo com origem, saida do porto, chegada prevista e disponibilidade
da carga. A saida mostra ATD quando a COSCO informa o horario realizado e ETD
quando ha apenas previsao. Marcos sem horario permanecem explicitamente sem
informacao; o desenho nao usa dados de outro embarque.

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

Quando o armador estiver em branco ou não puder ser reconhecido, o botão
**Buscar armador** consulta os portais públicos da COSCO, CMA CGM, HMM e Yang
Ming em sessões isoladas do Electron. Se houver BL cadastrado, ele tem
prioridade nessa busca. O JK envia a referência aos portais após o clique e
mostra o resultado de cada consulta. Só confirma correspondência quando o
portal apresenta a mesma referência junto com dados do embarque. Portais
indisponíveis aparecem separados das respostas sem correspondência.

Quando houver correspondência, **Ver em [armador]** abre o resultado no
navegador do JK. Se mais de um portal encontrar a referência, selecione o
resultado correspondente ao seu embarque. A busca preserva o campo Armador.
A versão web oferece links oficiais para a consulta manual.

## Posicao AIS e mapa

**Atualizar posição AIS** mostra os dados da Datalastic quando a API está
configurada. **Ver mapa AIS** coloca um marcador do último sinal no mapa
OpenStreetMap dentro do detalhe do embarque.

Sem chave da Datalastic, o aplicativo desktop abre a consulta pública em uma
janela do navegador do JK. Com IMO e MMSI, a consulta abre a ficha da Maritime
Optima. Quando a ficha não abre ou só há um identificador, o navegador pesquisa
pelo número no MyShipTracking. **Ver mapa AIS** tenta o mapa do VesselFinder
pelo IMO ou MMSI. Se ele não abrir e houver MMSI, tenta a ficha com mapa do Wingo
Marine. Quando a fonte exibe apenas resultados de busca, selecione a ficha
correspondente para ver a posição disponível e a data do sinal. Se o navegador
interno estiver indisponível, o painel oferece um link direto para o mapa público.

A consulta pública é iniciada pelo clique e envia apenas IMO e MMSI conforme
a página consultada. A sessão do navegador é isolada e a página original
apresenta os dados e horários da própria fonte.

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

Quando não há chave, o endpoint local responde com o código
`api_nao_configurada`, que aciona a consulta pública no aplicativo desktop.

## Endpoint interno

```text
GET /api/importacoes/rastreamento/navio?imo=9996678
GET /api/importacoes/rastreamento/navio?mmsi=249369000
```

No backend real, a rota reutiliza a dependencia de autenticacao do JK Sistema.
A resposta normalizada contem identificadores, coordenadas, velocidade, curso,
estado de navegacao, horario do ultimo sinal, destino e ETA informados pelo AIS.
