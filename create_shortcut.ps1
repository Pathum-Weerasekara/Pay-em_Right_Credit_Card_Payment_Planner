$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path -Path $DesktopPath -ChildPath "Pay'em Right - CC Payment Planner.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "c:\Users\pathu\OneDrive\Desktop\CC Payment Planner and Tracker\Run_App.bat"
$Shortcut.WorkingDirectory = "c:\Users\pathu\OneDrive\Desktop\CC Payment Planner and Tracker"
$Shortcut.Description = "Pay'em Right - CC Payment Planner Launcher"
$Shortcut.IconLocation = "shell32.dll, 220" # Credit Card / Wallet icon style from Windows shell
$Shortcut.Save()
Write-Host "Desktop shortcut created successfully!"
