Set-Location 'C:\Hermes Unreal\metin2_research'
$log = 'C:\Hermes Unreal\metin2_research\reports\control_panel_elevated_run.log'
'started elevated wrapper ' + (Get-Date -Format o) | Set-Content -Path $log -Encoding UTF8
& 'C:\Python312\python.exe' 'C:\Hermes Unreal\metin2_research\scripts\metin2_control_panel.py' --project-root 'C:\Hermes Unreal\metin2_research' *>> $log
'exited elevated wrapper ' + (Get-Date -Format o) | Add-Content -Path $log -Encoding UTF8
