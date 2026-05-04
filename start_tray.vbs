' BotGSTAR - Lanceur silencieux du tray watchdog
' Lance pythonw.exe (sans console) sur bot_tray.py
' Double-clic ce fichier OU placer un raccourci dans le dossier Startup
' (le menu tray "Démarrer avec Windows" le fait pour toi)

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Détection auto de pythonw.exe via PATH si possible, sinon chemin par défaut
pythonw = "pythonw.exe"
defaultPath = "C:\Users\Gstar\AppData\Local\Programs\Python\Python312\pythonw.exe"
If fso.FileExists(defaultPath) Then
    pythonw = defaultPath
End If

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = scriptDir
WshShell.Run """" & pythonw & """ """ & scriptDir & "\bot_tray.py""", 0, False
