Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 1. Garante que o script roda na pasta correta (onde estão as pastas 'info' e 'img')
CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = CurrentDirectory

' 2. Inicia o Streamlit totalmente oculto
' Alterado de App.py para app.py conforme a refatoração
WshShell.Run "cmd /c python -m streamlit run app.py --server.headless true --server.runOnSave false", 0

' 3. Aguarda 4 segundos (aumentei um pouco para garantir que módulos carreguem)
WScript.Sleep 4000

' 4. Abre o navegador em Modo Aplicativo
' Se preferir Chrome, troque "msedge" por "chrome"
WshShell.Run "msedge --new-window --app=http://localhost:8501", 0

Set WshShell = Nothing
Set fso = Nothing