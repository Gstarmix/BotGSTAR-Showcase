' Arsenal Intelligence Unit - Lanceur silencieux du Summarize GUI
' Lance pythonw.exe (sans console) sur summarize_gui.py
' Double-clic ce fichier pour ouvrir l'interface graphique.

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
WshShell.Run """" & pythonw & """ """ & scriptDir & "\summarize_gui.py""", 0, False
