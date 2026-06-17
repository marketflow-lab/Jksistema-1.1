# Regra de fonte unica dos HTML

## Regra oficial

A fonte oficial das telas HTML do sistema agora e a pasta `static/`.

O backend serve essa pasta em `backend_api.py`:

```python
app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

Entao, ao corrigir ou criar uma tela, edite primeiro:

```text
static/nome_da_tela.html
```

Os arquivos HTML na raiz continuam existindo apenas como espelhos legados para compatibilidade com empacotamento e fluxos antigos.

## Como sincronizar

Depois de editar qualquer HTML em `static/`, execute:

```bat
sincronizar_html.bat
```

Esse comando copia os HTML oficiais de `static/` para a raiz e valida se os hashes estao iguais.

Tambem e possivel rodar diretamente:

```bat
npm.cmd run source:sync
npm.cmd run source:verify
```

## Bloqueio obrigatorio

Antes de iniciar o app por `npm start`, rodar o Bug Hunter ou empacotar o Electron, a checagem de fonte unica precisa passar.

Se aparecer erro como:

```text
HTML fora da regra de fonte unica
```

edite a versao em `static/`, rode `sincronizar_html.bat` e tente novamente.

## Fluxo correto para mudancas de modulo

1. Edite `static/<modulo>.html`.
2. Rode `sincronizar_html.bat`.
3. Rode a validacao do modulo ou `npm.cmd run source:verify`.
4. Se estiver mexendo no app instalado, sincronize tambem `%APPDATA%\JK Sistema Cliente\local_app`.

## Por que isso existe

Antes, algumas mudancas eram feitas na raiz e outras em `static/`. Como o backend serve `static/`, isso criava divergencia: o codigo parecia corrigido no repositorio, mas o programa aberto podia estar usando outra copia.

Com esta regra, `static/` e a verdade unica para HTML.
