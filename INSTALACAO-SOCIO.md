# Instalar o MCP do Revit da Zeit neste computador

> **Como usar:** abra o Claude Code numa pasta qualquer deste PC (ex.: `C:\Users\<seu usuário>`), cole o conteúdo deste arquivo inteiro na conversa e mande executar. O Claude vai rodar os comandos abaixo um a um, conferindo cada etapa. As partes marcadas **[VOCÊ]** são as únicas que exigem ação humana.

---

## Instruções para o Claude Code

Você está instalando, na máquina do usuário, o mesmo MCP do Revit que já roda na máquina do Gabriel (Zeit Engenharia). É o projeto aberto `revit-mcp-server` (servidor Python/FastMCP) mais uma extensão do pyRevit que roda dentro do Revit e expõe a API pela porta 48884. O código está no fork público da Zeit no GitHub: `gabrieloliveira-crypto/zeit-revit-mcp-server` (branch `master`). Não precisa de conta no GitHub nem de login para clonar.

Regras:
- Rode os comandos pela ferramenta **PowerShell**, um de cada vez, e confira o resultado antes de seguir. PowerShell 5.1 não aceita `&&`.
- Não invente caminhos. Use `$env:USERPROFILE` e `$env:APPDATA` em vez de digitar o nome do usuário.
- Se uma etapa falhar, pare, mostre o erro e proponha a correção. Não pule etapas.
- Ao final, faça o teste da etapa 8 e só diga que terminou se ele passar.

### Etapa 0. Pré-requisitos (só conferir)

Confira e relate ao usuário o que existe e o que falta:

```powershell
Get-ChildItem "C:\Program Files\Autodesk" | Where-Object Name -like "Revit 20*" | Select-Object -ExpandProperty Name
git --version
Test-Path "C:\Program Files\pyRevit-Master"
Get-Command pyrevit -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
claude --version
```

Precisa de: Revit 2024, 2025, 2026 ou 2027; Git; pyRevit; Claude Code. Se faltar Git, instale com `winget install --id Git.Git -e --source winget`. Se faltar pyRevit, vá para a etapa 1. Se o Revit for 2023 ou anterior, pare: não é compatível.

### Etapa 1. pyRevit (só se não estiver instalado)

**[VOCÊ]** Baixe o instalador em https://github.com/pyrevitlabs/pyRevit/releases (o `.exe` mais recente, de preferência o `_admin_signed`) e instale aceitando os padrões. Depois diga ao Claude que terminou.

Claude: depois que o usuário confirmar, confira de novo com `Test-Path "C:\Program Files\pyRevit-Master"` e `pyrevit env`. Se o comando `pyrevit` não for encontrado, feche e abra o PowerShell de novo (o instalador acrescenta ao PATH).

### Etapa 2. Instalar o `uv` (gerenciador Python)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Depois confira (pode precisar reabrir o terminal para o PATH atualizar):

```powershell
& "$env:USERPROFILE\.local\bin\uv.exe" --version
```

### Etapa 3. Clonar o repositório da Zeit

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\zeit-dev" | Out-Null
git clone https://github.com/gabrieloliveira-crypto/zeit-revit-mcp-server.git "$env:USERPROFILE\zeit-dev\revit-mcp-server"
```

O repositório é público: o clone não pede login. Se o Git abrir uma janela pedindo conta do GitHub, feche a janela e rode o comando de novo; se mesmo assim falhar, avise o Gabriel.

Confira que clonou direito:

```powershell
Get-ChildItem "$env:USERPROFILE\zeit-dev\revit-mcp-server" | Select-Object -ExpandProperty Name
```

Tem que aparecer, entre outros, `main.py`, `startup.py`, `revit_mcp` e `pyproject.toml`.

### Etapa 4. Instalar as dependências Python

```powershell
& "$env:USERPROFILE\.local\bin\uv.exe" --directory "$env:USERPROFILE\zeit-dev\revit-mcp-server" sync
```

Se não houver Python 3.11 ou superior no PC, o `uv` baixa um sozinho. Ao terminar deve existir a pasta `.venv` dentro do repositório.

### Etapa 5. Instalar a extensão dentro do pyRevit

A extensão é simplesmente uma cópia da pasta do repositório dentro da pasta de extensões do pyRevit, com o nome terminando em `.extension` (o pyRevit só carrega `startup.py` e a pasta `revit_mcp`; o resto não atrapalha).

```powershell
$ext = "$env:APPDATA\pyRevit\Extensions\mcp-server-for-revit-python.extension"
New-Item -ItemType Directory -Force "$env:APPDATA\pyRevit\Extensions" | Out-Null
if (Test-Path $ext) { Rename-Item $ext "$ext.bak-$(Get-Date -Format yyyyMMdd-HHmm)" }
Copy-Item "$env:USERPROFILE\zeit-dev\revit-mcp-server" $ext -Recurse -Exclude ".venv",".git",".pytest_cache","__pycache__"
Test-Path "$ext\startup.py"; Test-Path "$ext\revit_mcp"
```

Os dois `Test-Path` têm que devolver `True`.

### Etapa 6. Ligar o servidor Routes do pyRevit

```powershell
pyrevit configs routes enable
pyrevit configs routes port 48884
```

Se der erro de permissão (a configuração fica em `C:\ProgramData\pyRevit\pyRevit_config.ini` quando o pyRevit foi instalado para todos os usuários), **[VOCÊ]** abra um PowerShell como administrador e rode os dois comandos lá. Alternativa pela interface: no Revit, aba pyRevit, botão Settings, seção Routes, marcar "Enable Routes Server", salvar.

Conferir:

```powershell
Select-String -Path "C:\ProgramData\pyRevit\pyRevit_config.ini","$env:APPDATA\pyRevit\pyRevit_config.ini" -Pattern "^\[routes\]" -Context 0,2 -ErrorAction SilentlyContinue
```

Tem que aparecer `enabled = true`.

### Etapa 7. Cadastrar o servidor no Claude Code

```powershell
claude mcp add revit --scope user -e UV_LINK_MODE=copy -- "$env:USERPROFILE\.local\bin\uv.exe" --directory "$env:USERPROFILE\zeit-dev\revit-mcp-server" run main.py
```

Conferir:

```powershell
claude mcp get revit
```

### Etapa 8. Teste final

**[VOCÊ]** Abra o Revit (um só; se abrir dois, o segundo pega a porta 48885 e o MCP não acha) e abra qualquer projeto `.rvt`. Espere a aba pyRevit carregar na faixa de opções. Depois diga ao Claude que está pronto.

Claude: confira primeiro pelo navegador do sistema, sem o Claude Code no meio:

```powershell
Invoke-RestMethod "http://localhost:48884/revit_mcp/status/"
```

Tem que voltar um JSON com o estado do Revit. Se der `Unable to connect`, veja a seção "Problemas comuns" abaixo.

Depois reinicie o Claude Code (feche e abra a conversa) para carregar o MCP e peça: "qual o status do Revit e qual o nome do modelo aberto?". Ele deve responder usando as ferramentas `get_revit_status` e `get_revit_model_info`. Se funcionar, a instalação acabou.

---

## Problemas comuns (aprendidos na máquina do Gabriel)

**"No active Revit document" ou "All connection attempts failed" com o arquivo aberto.** O pyRevit subiu o Routes em outra porta, em geral a 48885 (segundo Revit aberto, ou porta ocupada). Descubra a porta pelo processo do Revit:

```powershell
$pid_revit = (Get-Process Revit).Id; Get-NetTCPConnection -State Listen -OwningProcess $pid_revit | Select-Object LocalPort
```

Se for 48885, feche os Revits extras e abra um só. Em último caso, o `main.py` tem `REVIT_PORT = 48884` fixo na linha 22; pode trocar lá.

**Aba pyRevit não aparece no Revit.** pyRevit não carregou. Rode `pyrevit attach master default --installed` num PowerShell como administrador e abra o Revit de novo.

**Extensão não carrega (sem erro, mas `/status/` dá 404).** Confira que a pasta se chama exatamente `mcp-server-for-revit-python.extension` e que `startup.py` está na raiz dela. No Revit, aba pyRevit, botão Reload.

**O servidor devolve "success" mas a mudança não ficou no modelo.** O endpoint engole falhas do Revit e desfaz tudo em silêncio. Causas clássicas: elemento dentro de grupo de modelo, parede com altura quase zero. Sempre conferir se gravou numa chamada separada, e trabalhar em lotes pequenos.

**Acentos corrompem.** Textos com acento mandados pelo código chegam errados no Revit (`Ã¡`) e caminhos com acento criam pastas duplicadas. Montar acentos com `unichr(0xE1)` etc. e derivar caminhos de `doc.PathName`, nunca digitar "Área de Trabalho" no código.

**Cada chamada já abre uma transação.** Não criar `DB.Transaction` própria no código enviado (erro "Starting a new transaction is not permitted").

**`ElementId(int)` ambíguo no IronPython.** Usar `DB.ElementId(System.Int64(id))`. `ElementId.IntegerValue` não existe no Revit 2024 em diante: usar `.Value`.
