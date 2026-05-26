# ðŸ“ SINCRONIZAÃ‡ÃƒO DE ARQUIVOS HTML

## âš ï¸ O Problema

O sistema tem **arquivos HTML em dois lugares**:
- **Raiz do projeto** (ex: `vendas.html`)
- **DiretÃ³rio `/static`** (ex: `static/vendas.html`)

O backend (`backend_api.py`) estÃ¡ configurado para servir arquivos **do diretÃ³rio `/static`**:

```python
app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

**Resultado:** Se vocÃª edita `vendas.html` na raiz, as mudanÃ§as **nÃ£o aparecem** no navegador porque o servidor estÃ¡ servindo a versÃ£o antiga do diretÃ³rio `/static`.

## âœ… SoluÃ§Ã£o

Sempre manter os arquivos HTML **sincronizados** entre a raiz e `/static`.

### **OpÃ§Ã£o 1: Script AutomÃ¡tico** (Recomendado)

Execute antes de iniciar o servidor:
```bash
sincronizar_html.bat
```

Este script copia automaticamente todos os arquivos HTML da raiz para `/static`.

### **OpÃ§Ã£o 2: Copiar Manualmente**

```powershell
# Copiar um arquivo especÃ­fico
Copy-Item vendas.html static/ -Force

# Copiar todos os arquivos .html
Get-ChildItem *.html | Copy-Item -Destination static/ -Force
```

### **OpÃ§Ã£o 3: Editar Diretamente em `/static`**

Se preferir, vocÃª pode editar os arquivos diretamente em `/static`:
```
static/vendas.html
static/dashboard.html
static/debug_vendas.html
...
```

## ðŸ“‹ Arquivos que Precisam Ser Sincronizados

| Arquivo | LocalizaÃ§Ã£o |
|---------|------------|
| `vendas.html` | Raiz + `/static` |
| `vendas_sku.html` | Raiz + `/static` |
| `devolucoes.html` | Raiz + `/static` |
| `devolucoes_sku.html` | Raiz + `/static` |
| `estoque.html` | Raiz + `/static` |
| `renovacao.html` | Raiz + `/static` |
| `integracoes.html` | Raiz + `/static` |
| `promo.html` | Raiz + `/static` |
| `frontend_promo.html` | Raiz + `/static` |
| `frontend_etiquetas.html` | Raiz + `/static` |
| `dashboard.html` | Raiz + `/static` |
| `debug_vendas.html` | Raiz + `/static` |
| `frontend_index.html` | Apenas em `/static` |

## ðŸ”§ PrÃ³ximos Passos

1. **Execute o script de sincronizaÃ§Ã£o:**
   ```bash
   sincronizar_html.bat
   ```

2. **Reinicie o servidor:**
   ```bash
   uvicorn backend_api:app --reload --port 8001
   ```

3. **Acesse o mÃ³dulo de vendas** e verifique se as mudanÃ§as aparecem

## ðŸ’¡ Dica Importante

Sempre que vocÃª:
- âœï¸ Editar um arquivo HTML na raiz
- ðŸ› Corrigir um bug em um mÃ³dulo
- âž• Adicionar uma nova funcionalidade

**Execute `sincronizar_html.bat`** antes de testar no navegador!

## ðŸ“Š Status Atual

âœ… Todos os arquivos HTML foram sincronizados para `/static`

VocÃª agora pode ver o mÃ³dulo de vendas **com o design/layout correto**! ðŸŽ‰

---

**Criado:** 4 de fevereiro de 2026
