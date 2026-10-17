<#
Unified PowerShell entrypoint for BGP Router-ID fault injection/recovery.
Requires an authorized lab environment, Python, SSH_* variables, and device_connector.
Run from any working directory. No credentials are stored in this script.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('inject','recover')]
    [string]$Action,

    [Parameter(Mandatory=$true)]
    [string]$HostIp,

    [string]$Peer,
    [int]$Asn = 100,
    [string]$PeerSubnet,
    [string]$StateFile,
    [string]$PythonCommand = 'python'
)

$ErrorActionPreference = 'Stop'
$pyScript = Join-Path $PSScriptRoot 'bgp_router_id.py'
$stateDir = Join-Path $PSScriptRoot 'fault_states'
$configFile = Join-Path $PSScriptRoot 'device.env.ps1'
if (Test-Path -LiteralPath $configFile -PathType Leaf) {
    . $configFile
}
if (-not (Test-Path -LiteralPath $pyScript -PathType Leaf)) { throw "Missing Python script: $pyScript" }
if (-not $env:SSH_USERNAME) { throw 'SSH_USERNAME is missing. Configure device.env.ps1 locally or set the environment variables.' }
if (-not (Test-Path -LiteralPath $stateDir)) { New-Item -ItemType Directory -Path $stateDir | Out-Null }

# A flag required by the existing Python CLI; it is NOT proof that the connector's
# [Y/N] handling is correct. Verify device_connector before enabling live writes.
$env:DEVICE_CONNECTOR_INTERACTIVE_OK = '1'

if ($Action -eq 'inject') {
    if (-not $Peer) { throw 'Inject requires -Peer.' }
    if (-not $PeerSubnet) { $PeerSubnet = "$Peer/32" }
    if (-not $StateFile) {
        $safeHost = $HostIp -replace '[^A-Za-z0-9_.-]', '_'
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fffffff'
        $StateFile = Join-Path $stateDir ("bgp_{0}_{1}_{2}.json" -f $safeHost, $stamp, [guid]::NewGuid().ToString('N').Substring(0,8))
    }
    if (Test-Path -LiteralPath $StateFile) { throw "State file already exists: $StateFile" }
    $cmdArgs = @($pyScript, 'inject', '--host', $HostIp, '--asn', "$Asn", '--peer', $Peer, '--state', $StateFile, '--apply')
    if ($PeerSubnet) { $cmdArgs += @('--peer-subnet', $PeerSubnet) }
    Write-Host "Injecting into $HostIp; recovery state will be: $StateFile"
} else {
    if (-not $StateFile) {
        $matches = @(
            Get-ChildItem -LiteralPath $stateDir -Filter '*.json' -File | ForEach-Object {
                try {
                    $data = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                    if (($data.host -eq $HostIp) -and ([int]$data.asn -eq $Asn) -and
                        (-not $Peer -or $data.peer -eq $Peer) -and
                        ($data.status -in @('apply_started','injected','verify_failed','recovery_started','recovery_failed'))) {
                        $_.FullName
                    }
                } catch { Write-Warning "Skipping unreadable state file: $($_.FullName)" }
            }
        )
        if ($matches.Count -eq 0) { throw 'No unresolved state record found. Supply -StateFile explicitly.' }
        if ($matches.Count -gt 1) { throw "More than one unresolved state record; supply -StateFile: $($matches -join ', ')" }
        $StateFile = $matches[0]
    }
    if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) { throw "State file not found: $StateFile" }
    $record = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.host -ne $HostIp -or [int]$record.asn -ne $Asn -or ($Peer -and $record.peer -ne $Peer)) {
        throw 'State file does not match host/ASN/peer.'
    }
    $cmdArgs = @($pyScript, 'recover', '--host', $HostIp, '--asn', "$Asn", '--state', $StateFile, '--apply')
    Write-Host "Recovering $HostIp using: $StateFile"
}

& $PythonCommand @cmdArgs
if ($LASTEXITCODE -ne 0) {
    throw "Python failed (exit code $LASTEXITCODE). Check the device's actual Router-ID/peers before retrying. State: $StateFile"
}
Write-Host "Command completed. State: $StateFile"