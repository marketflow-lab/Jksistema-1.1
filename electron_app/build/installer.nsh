!include "LogicLib.nsh"

!ifndef BUILD_UNINSTALLER
  !macro jkVcRuntimeIsCurrent RESULT
    StrCpy ${RESULT} "0"
    ${If} ${FileExists} "$INSTDIR\resources\local_app\prerequisites\VC_redist.x64.exe"
      ClearErrors
      GetDLLVersion "$INSTDIR\resources\local_app\prerequisites\VC_redist.x64.exe" $6 $7
      ${IfNot} ${Errors}
        ClearErrors
        ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
        ReadRegDWORD $2 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Major"
        ReadRegDWORD $3 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Minor"
        ReadRegDWORD $4 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Bld"
        ReadRegDWORD $5 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Rbld"
        ${IfNot} ${Errors}
          IntOp $8 $6 >> 16
          IntOp $9 $6 & 0xFFFF
          IntOp $R0 $7 >>> 16
          IntOp $R1 $7 & 0xFFFF
          ${If} $0 == 1
            ${If} $2 > $8
              StrCpy ${RESULT} "1"
            ${ElseIf} $2 == $8
              ${If} $3 > $9
                StrCpy ${RESULT} "1"
              ${ElseIf} $3 == $9
                ${If} $4 > $R0
                  StrCpy ${RESULT} "1"
                ${ElseIf} $4 == $R0
                  ${If} $5 >= $R1
                    StrCpy ${RESULT} "1"
                  ${EndIf}
                ${EndIf}
              ${EndIf}
            ${EndIf}
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
  !macroend

!macro customInstall
  SetDetailsPrint both
  SetRegView 64
  CreateDirectory "$APPDATA\JK Sistema Cliente\local_app"
  CreateDirectory "$APPDATA\JK Sistema Cliente\local_app\info"
  CreateDirectory "$APPDATA\JK Sistema Cliente\local_app\logs"

  StrCpy $R8 "$APPDATA\JK Sistema Cliente\local_app\logs\python-runtime-install.log"
  FileOpen $R9 "$R8" a
  FileSeek $R9 0 END
  FileWrite $R9 "$\r$\n==== JK Sistema Python runtime installer ====$\r$\n"
  FileWrite $R9 "Source: $INSTDIR\resources\local_app$\r$\n"
  FileWrite $R9 "Target: $APPDATA\JK Sistema Cliente\local_app$\r$\n"
  FileClose $R9

  !insertmacro jkVcRuntimeIsCurrent $R7
  ${If} $R7 != 1
    ${IfNot} ${FileExists} "$INSTDIR\resources\local_app\prerequisites\VC_redist.x64.exe"
      FileOpen $R9 "$R8" a
      FileSeek $R9 0 END
      ${If} ${isUpdated}
        FileWrite $R9 "AVISO: Microsoft Visual C++ Runtime ausente no computador e no pacote; atualizacao continuara e o primeiro boot registrara o diagnostico.$\r$\n"
        FileClose $R9
        DetailPrint "Aviso: Microsoft Visual C++ Runtime nao confirmado; consulte o log no primeiro inicio."
      ${Else}
        FileWrite $R9 "ERRO: Microsoft Visual C++ Runtime ausente no computador e no instalador.$\r$\n"
        FileClose $R9
        ${IfNot} ${Silent}
          MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "O Microsoft Visual C++ Runtime obrigatorio nao foi encontrado.$\r$\n$\r$\nReinstale usando o pacote completo da versao ${VERSION}.$\r$\nLog: $R8"
        ${EndIf}
        SetErrorLevel 13
        Abort
      ${EndIf}
    ${Else}
      DetailPrint "Instalando o Microsoft Visual C++ Runtime..."
      ClearErrors
      ExecShellWait "runas" "$INSTDIR\resources\local_app\prerequisites\VC_redist.x64.exe" "/install /quiet /norestart" SW_HIDE
      ${If} ${Errors}
        FileOpen $R9 "$R8" a
        FileSeek $R9 0 END
        ${If} ${isUpdated}
          FileWrite $R9 "AVISO: nao foi possivel executar o instalador do Microsoft Visual C++ Runtime; atualizacao continuara sem Abort.$\r$\n"
          FileClose $R9
          DetailPrint "Aviso: instalacao do Microsoft Visual C++ Runtime nao concluida; consulte o log no primeiro inicio."
        ${Else}
          FileWrite $R9 "ERRO: nao foi possivel executar o instalador do Microsoft Visual C++ Runtime.$\r$\n"
          FileClose $R9
          ${IfNot} ${Silent}
            MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "Nao foi possivel executar o instalador do Microsoft Visual C++ Runtime.$\r$\n$\r$\nLog: $R8"
          ${EndIf}
          SetErrorLevel 14
          Abort
        ${EndIf}
      ${Else}
        !insertmacro jkVcRuntimeIsCurrent $R7
        ${If} $R7 != 1
          FileOpen $R9 "$R8" a
          FileSeek $R9 0 END
          ${If} ${isUpdated}
            FileWrite $R9 "AVISO: Microsoft Visual C++ Runtime continuou ausente ou desatualizado; atualizacao continuara sem Abort.$\r$\n"
            FileClose $R9
            DetailPrint "Aviso: Microsoft Visual C++ Runtime nao confirmado; consulte o log no primeiro inicio."
          ${Else}
            FileWrite $R9 "ERRO: Microsoft Visual C++ Runtime continuou ausente ou desatualizado apos o instalador concluir.$\r$\n"
            FileClose $R9
            ${IfNot} ${Silent}
              MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "O Microsoft Visual C++ Runtime nao foi confirmado apos a instalacao.$\r$\n$\r$\nLog: $R8"
            ${EndIf}
            SetErrorLevel 19
            Abort
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}

  ${If} ${isUpdated}
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "INFO: atualizacao detectada; provisionamento adiado para o self-heal transacional no primeiro inicio.$\r$\n"
    FileClose $R9
    DetailPrint "Atualizacao instalada; o ambiente Python sera validado no primeiro inicio."
  ${Else}
    ${If} $installMode == "all"
      FileOpen $R9 "$R8" a
      FileSeek $R9 0 END
      FileWrite $R9 "INFO: instalacao para todos os usuarios; .venv per-user sera criada pelo self-heal transacional no primeiro inicio de cada usuario.$\r$\n"
      FileClose $R9
      DetailPrint "Runtime do sistema instalado; o ambiente Python sera preparado no primeiro inicio de cada usuario."
    ${Else}
  ${IfNot} ${FileExists} "$INSTDIR\resources\local_app\python_runtime\portable\python.exe"
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "ERRO: Python portatil nao encontrado no instalador.$\r$\n"
    FileClose $R9
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "O instalador esta sem o Python portatil obrigatorio.$\r$\n$\r$\nReinstale usando o pacote completo da versao ${VERSION}.$\r$\nLog: $R8"
    ${EndIf}
    SetErrorLevel 10
    Abort
  ${EndIf}

  ${IfNot} ${FileExists} "$INSTDIR\resources\local_app\scripts\provision_python_runtime.py"
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "ERRO: provision_python_runtime.py nao encontrado no instalador.$\r$\n"
    FileClose $R9
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "O instalador esta sem o provisionador do ambiente Python.$\r$\n$\r$\nReinstale usando o pacote completo da versao ${VERSION}.$\r$\nLog: $R8"
    ${EndIf}
    SetErrorLevel 11
    Abort
  ${EndIf}

  DetailPrint "Preparando o ambiente Python offline do JK Sistema..."
  StrCpy $R6 "$APPDATA\JK Sistema Cliente\local_app\info\installer-python-runtime.incomplete"
  FileOpen $R9 "$R6" w
  FileWrite $R9 "version=${VERSION}$\r$\nstate=provisioning$\r$\nlog=$R8$\r$\n"
  FileClose $R9
  ClearErrors
  ExecWait '"$INSTDIR\resources\local_app\python_runtime\portable\python.exe" -B -I "$INSTDIR\resources\local_app\scripts\provision_python_runtime.py" --source-root "$INSTDIR\resources\local_app" --target-root "$APPDATA\JK Sistema Cliente\local_app" --log-file "$R8"' $R0
  ${If} ${Errors}
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "ERRO: Windows nao conseguiu executar o provisionador Python.$\r$\n"
    FileClose $R9
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "O Windows nao conseguiu executar o provisionador Python.$\r$\n$\r$\nLog: $R8"
    ${EndIf}
    SetErrorLevel 15
    Abort
  ${EndIf}
  ${If} $R0 != 0
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "ERRO: provisionador finalizou com codigo $R0.$\r$\n"
    FileClose $R9
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "Nao foi possivel preparar o ambiente Python do JK Sistema.$\r$\n$\r$\nCodigo: $R0$\r$\nLog: $R8$\r$\nStatus: $APPDATA\JK Sistema Cliente\local_app\info\python-runtime-status.json"
    ${EndIf}
    SetErrorLevel $R0
    Abort
  ${EndIf}

  InitPluginsDir
  FileOpen $R5 "$PLUGINSDIR\verify-jk-python-runtime.py" w
  FileWrite $R5 "import hashlib,json,pathlib,sys$\r$\n"
  FileWrite $R5 "j=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))$\r$\n"
  FileWrite $R5 "s,t=map(pathlib.Path,sys.argv[1:3])$\r$\n"
  FileWrite $R5 "v=j(s/'runtime-versions.json')['python']; r=j(s/'runtime-manifest.json')['python']['portable']; w=s/'python_wheels'/'manifest.json'; q=j(w)$\r$\n"
  FileWrite $R5 "a=j(t/'info'/'python-runtime-status.json'); m=j(t/'.venv'/'.jk-venv-ready.json')$\r$\n"
  FileWrite $R5 "e=(v['version'],v['abi'],r['tree_sha256'],q['requirements_sha256'],hashlib.sha256(w.read_bytes()).hexdigest())$\r$\n"
  FileWrite $R5 "k=lambda x:(x.get('python_version'),x.get('python_abi'),x.get('runtime_tree_sha256'),x.get('requirements_sha256'),x.get('wheel_manifest_sha256'))$\r$\n"
  FileWrite $R5 "ok=a.get('state')==m.get('state')=='ready' and k(a)==k(m)==e and (t/'.venv'/'Scripts'/'python.exe').is_file() and (t/'.python-runtime'/'python.exe').is_file()$\r$\n"
  FileWrite $R5 "raise SystemExit(0 if ok else 1)$\r$\n"
  FileClose $R5
  ClearErrors
  ExecWait '"$INSTDIR\resources\local_app\python_runtime\portable\python.exe" -B -I "$PLUGINSDIR\verify-jk-python-runtime.py" "$INSTDIR\resources\local_app" "$APPDATA\JK Sistema Cliente\local_app"' $R0
  ${If} ${Errors}
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "ERRO: Windows nao conseguiu executar a validacao final da .venv.$\r$\n"
    FileClose $R9
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "Nao foi possivel validar o ambiente Python apos a instalacao.$\r$\n$\r$\nLog: $R8"
    ${EndIf}
    SetErrorLevel 16
    Abort
  ${EndIf}
  ${If} $R0 != 0
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "ERRO: status, versao ou hashes da .venv nao correspondem ao pacote instalado.$\r$\n"
    FileClose $R9
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "O ambiente Python nao foi validado apos a instalacao.$\r$\n$\r$\nCodigo: $R0$\r$\nLog: $R8"
    ${EndIf}
    SetErrorLevel 17
    Abort
  ${EndIf}

    Delete "$R6"
    FileOpen $R9 "$R8" a
    FileSeek $R9 0 END
    FileWrite $R9 "OK: ambiente Python e .venv validados para ${VERSION}.$\r$\n"
    FileClose $R9
    DetailPrint "Ambiente Python offline preparado e validado."
    ${EndIf}
  ${EndIf}
!macroend
!endif
