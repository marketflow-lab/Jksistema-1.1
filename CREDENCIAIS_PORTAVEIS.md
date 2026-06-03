# Credenciais portaveis

Este pacote exporta chaves, tokens e configs locais para um arquivo criptografado
`.jkcred`. O repositorio continua limpo: o arquivo gerado nao deve ir para o Git.

## Exportar na maquina configurada

```bat
ExportarCredenciais.bat --out credenciais-jk.jkcred
```

O comando pede uma senha e salva um pacote criptografado com os arquivos locais
encontrados, como `.env`, chaves em `info/`, configs de Google Sheets e configs
de integracoes por cliente. O pacote tambem leva a autorizacao local de usuarios
(`info/auth_users.db` e caches locais), para preservar as permissoes dos modulos
na outra maquina.

Para conferir antes:

```bat
ListarCredenciais.bat
```

## Importar na maquina final

Depois de baixar o app limpo do GitHub, copie o `.jkcred` para a pasta do projeto
e rode:

```bat
ImportarCredenciais.bat --in credenciais-jk.jkcred
```

Se o arquivo estiver na pasta com o nome `credenciais-jk.jkcred`, basta executar:

```bat
ImportarCredenciais.bat
```

No aplicativo desktop, a mesma acao tambem fica no sidebar do usuario:
`Importar credenciais`. Se o dashboard abrir sem nenhum modulo, a mesma opcao
aparece no centro da tela.

O importador pede a mesma senha, restaura os arquivos nas mesmas pastas e cria
backup automatico dos arquivos locais substituidos em `backups/`.
Depois da importacao, saia e entre novamente para o dashboard recarregar as
permissoes.

## Instalador normal com Firebase

Ao rodar `GerarExecutavel.bat`, o build valida se existe uma service account do
Firebase na raiz do projeto. Exemplos de nomes aceitos:

```text
jkjkjk-485920-e598a0a0dcb9.json
firebase-service-account.json
firebase_service_account.json
```

Esse JSON entra no `local_app` do instalador normal. Na outra maquina, o backend
local encontra essa credencial, ativa `JK_ACCESS_BACKEND=firebase` e consulta os
usuarios diretamente no Firestore.

## Opcoes uteis

```bat
ExportarCredenciais.bat --out C:\seguro\credenciais.jkcred --force
ImportarCredenciais.bat --in C:\seguro\credenciais.jkcred --force
```

Tambem da para usar:

```bat
set JK_CREDENTIALS_PASSWORD=minha-senha-forte
npm run credenciais:exportar -- --out credenciais-jk.jkcred
npm run credenciais:importar -- --in credenciais-jk.jkcred
```

Use uma senha forte e transfira o `.jkcred` por um canal privado.
