[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ConfigPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Workspace,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$AuthFile,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PluginHome,

    [string]$LogRoot = "",

    [ValidateRange(1, 600)]
    [int]$DependencyTimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$runtimeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$resolvedAuth = (Resolve-Path -LiteralPath $AuthFile).Path
$resolvedPluginHome = (Resolve-Path -LiteralPath $PluginHome).Path

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Candidate Python runtime is missing"
}
if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
    throw "Akashic workspace is missing"
}
if (
    -not (
        Test-Path `
            -LiteralPath (Join-Path $resolvedPluginHome "manifest.toml") `
            -PathType Leaf
    )
) {
    throw "Candidate plugin manifest is missing"
}
if ([string]::IsNullOrWhiteSpace($LogRoot)) {
    $LogRoot = Join-Path (Split-Path -Parent $resolvedConfig) "runtime"
}
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

$supervisorLog = Join-Path $LogRoot "akashic-supervisor.log"

function Write-SupervisorEvent {
    param([Parameter(Mandatory = $true)][string]$Message)

    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Message
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding UTF8
}

function Wait-LoopbackPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $connect = $client.ConnectAsync("127.0.0.1", $Port)
            if ($connect.Wait(1000) -and $client.Connected) {
                return
            }
        }
        catch {
            # The owning dependency has its own startup task; retry until deadline.
        }
        finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    throw "Required loopback dependency did not become ready"
}

try {
    Wait-LoopbackPort -Port 8317 -TimeoutSeconds $DependencyTimeoutSeconds
    Wait-LoopbackPort -Port 11434 -TimeoutSeconds $DependencyTimeoutSeconds

    $env:AKASHIC_AUTH_FILE = $resolvedAuth
    $env:AKASHIC_PLUGIN_HOME = $resolvedPluginHome
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdoutLog = Join-Path $LogRoot "akashic-$stamp.out.log"
    $stderrLog = Join-Path $LogRoot "akashic-$stamp.err.log"
    Write-SupervisorEvent "starting candidate runtime"

    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "main.py",
            "--config", "`"$resolvedConfig`"",
            "--workspace", "`"$Workspace`""
        ) `
        -WorkingDirectory $runtimeRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -Wait `
        -PassThru

    Write-SupervisorEvent (
        "candidate runtime exited pid={0} code={1}" -f
            $process.Id, $process.ExitCode
    )
}
catch {
    Write-SupervisorEvent (
        "candidate launcher failed type={0}" -f
            $_.Exception.GetType().Name
    )
}

# Any completed launcher run is unexpected. The Scheduled Task owns bounded
# restart cadence and MultipleInstances=IgnoreNew prevents duplicate pollers.
exit 1
