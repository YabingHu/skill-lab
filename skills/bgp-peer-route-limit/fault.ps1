<# BGP Peer Route-Limit lab runner. Inject/recover with saved JSON. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('inject','recover')][string]$Action,
    [Parameter(Mandatory=$true)][string]$HostIp,
    [string]$PeerHost,
    [string]$Peer,
    [int]$Asn = 200,
    [string[]]$Prefixes = @('1.2.3.4/32','2.2.3.4/32'),
    [string]$StateFile,
    [string]$PythonCommand = 'python',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$scriptFile = Join-Path $PSScriptRoot 'bgp_route_limit.py'
$states = Join-Path $PSScriptRoot 'fault_states'
$envFile = Join-Path $PSScriptRoot 'device.env.ps1'
if (Test-Path -LiteralPath $envFile -PathType Leaf) { . $envFile }
if (-not $env:SSH_USERNAME) { throw 'Missing SSH_USERNAME; set device.env.ps1.' }
if (-not (Test-Path -LiteralPath $scriptFile -PathType Leaf)) { throw "Missing $scriptFile" }
if (-not (Test-Path -LiteralPath $states)) { New-Item -ItemType Directory -Path $states | Out-Null }
$env:DEVICE_CONNECTOR_INTERACTIVE_OK = '1'

if ($Action -eq 'inject') {
    if (-not $PeerHost -or -not $Peer) { throw 'Inject requires -PeerHost (SSH address) and -Peer (BGP address).' }
    if ($Prefixes.Count -ne 2) { throw 'Exactly two prefixes are required.' }
    if (-not $StateFile) {
        $safeHost = $HostIp -replace '[^A-Za-z0-9_.-]', '_'
        $StateFile = Join-Path $states ("limit_{0}_{1}_{2}.json" -f $safeHost,(Get-Date -Format 'yyyyMMdd_HHmmss_fffffff'),[guid]::NewGuid().ToString('N').Substring(0,8))
    }
    if (Test-Path -LiteralPath $StateFile) { throw 'State file already exists; refusing to overwrite.' }
    $argsPy = @($scriptFile,'inject','--host',$HostIp,'--peer-host',$PeerHost,'--peer',$Peer,'--asn',"$Asn",'--state',$StateFile)
    foreach ($p in $Prefixes) { $argsPy += @('--prefix',$p) }
} else {
    if (-not $StateFile) {
        $matches = @(
            Get-ChildItem -LiteralPath $states -Filter '*.json' -File | ForEach-Object {
                try {
                    $s = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($s.scenario -eq 'bgp-peer-route-limit' -and $s.host -eq $HostIp -and [int]$s.asn -eq $Asn -and
                        (-not $PeerHost -or $s.peer_host -eq $PeerHost) -and (-not $Peer -or $s.peer -eq $Peer) -and
                        $s.status -in @('limit_setting','peer_announcing','injected','verify_incomplete','recovery_started','recovery_incomplete')) {
                        $_.FullName
                    }
                } catch { Write-Warning "Cannot read $($_.FullName)" }
            }
        )
        if ($matches.Count -ne 1) { throw "Expected exactly one outstanding state, found $($matches.Count). Specify -StateFile." }
        $StateFile = $matches[0]
    }
    if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) { throw "Missing state: $StateFile" }
    $argsPy = @($scriptFile,'recover','--host',$HostIp,'--asn',"$Asn",'--state',$StateFile)
    if ($PeerHost) { $argsPy += @('--peer-host',$PeerHost) }
}
if (-not $DryRun) { $argsPy += '--apply' }
Write-Host "Action: $Action; state: $StateFile; dry-run: $DryRun"
& $PythonCommand @argsPy
if ($LASTEXITCODE -ne 0) { throw "Python exit $LASTEXITCODE; check device state before retrying: $StateFile" }
