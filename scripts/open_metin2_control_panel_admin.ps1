$ErrorActionPreference = 'Stop'
$ProjectRoot = 'C:\Hermes Unreal\metin2_research'
$Python = 'C:\Python312\python.exe'
$Port = 8767
$BaseUrl = "http://127.0.0.1:$Port"
$LogDir = Join-Path $ProjectRoot 'reports\launcher_logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'metin2_control_panel_admin_launcher.log'
function Log($Message) { "$(Get-Date -Format o) $Message" | Out-File -FilePath $Log -Encoding UTF8 -Append }

try {
  $IsElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  Log "launcher start user=$([Security.Principal.WindowsIdentity]::GetCurrent().Name) elevated=$IsElevated base=$BaseUrl"

  $servers = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -like 'python*.exe' -and
      $_.CommandLine -like '*-m metin2_dashboard.server*' -and
      ($_.CommandLine -like '*--port 8767*' -or $_.CommandLine -like '*--port 8768*')
    }
  foreach ($server in $servers) {
    Log "stopping stale dashboard server pid=$($server.ProcessId) cmd=$($server.CommandLine)"
    Stop-Process -Id $server.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 500

  Set-Location $ProjectRoot

  Log "starting API $BaseUrl"
  # Use --project-root . so PowerShell Start-Process cannot split the path with spaces.
  $serverArgs = '-m metin2_dashboard.server --host 127.0.0.1 --port ' + $Port + ' --project-root .'
  $serverProc = Start-Process -FilePath $Python -ArgumentList $serverArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
  Log "API pid=$($serverProc.Id) args=$serverArgs"

  $ready = $false
  for ($i = 0; $i -lt 40; $i++) {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/scripts" -TimeoutSec 1 | Out-Null
      $ready = $true
      break
    } catch {
      Start-Sleep -Milliseconds 250
    }
  }
  Log "API ready=$ready"

  Log "starting native panel"
  $panelArgs = 'scripts\metin2_control_panel.py --base-url ' + $BaseUrl + ' --project-root .'
  $panelProc = Start-Process -FilePath $Python -ArgumentList $panelArgs -WorkingDirectory $ProjectRoot -PassThru
  Log "panel pid=$($panelProc.Id) args=$panelArgs"
  Log "launcher exit"
} catch {
  Log "ERROR $($_.Exception.GetType().FullName): $($_.Exception.Message)"
  throw
}
