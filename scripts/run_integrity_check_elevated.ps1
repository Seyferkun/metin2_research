$ErrorActionPreference = 'Continue'
Set-Location 'C:\Hermes Unreal\metin2_research'
$env:INTEGRITY_CHECK_OUT = 'C:\Hermes Unreal\metin2_research\reports\integrity_check_elevated.json'
& 'C:\Python312\python.exe' 'C:\Hermes Unreal\metin2_research\scripts\check_process_integrity.py' *> 'C:\Hermes Unreal\metin2_research\reports\integrity_check_elevated.stdout.txt'
