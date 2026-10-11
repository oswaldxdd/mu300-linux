[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$VerifyOnly,
    [string]$Work = '',
    [ValidateSet('en','tr','zh')][string]$Lang = 'zh'
)

$ErrorActionPreference = 'Stop'
$Package = $PSScriptRoot
$Release = Join-Path $Package 'release'
function Stop-Install([string]$Message) { throw "U30 Air offline package: $Message" }

# Verify the package before invoking the upstream installer. This entry point never downloads or installs
# host dependencies; missing tools or assets are reported before it can reach any device write.
$sumFile = Join-Path $Release 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $sumFile -PathType Leaf)) { Stop-Install 'release/SHA256SUMS is missing.' }
$expected = @{}
foreach ($line in [IO.File]::ReadAllLines($sumFile)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?([^\s]+)$') { $expected[$Matches[2]] = $Matches[1].ToLowerInvariant() }
}
$required = @('mu300-kernel.tar.gz','mu300-kernel-7.2.tar.gz','mu300-openwrt-luci-rootfs.tar.gz','mu300-update','BUILD-MANIFEST.json')
foreach ($name in $required) {
    $path = Join-Path $Release $name
    if (-not $expected.ContainsKey($name)) { Stop-Install "SHA256SUMS has no entry for $name." }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Stop-Install "release/$name is missing; this installer is offline and will not fetch it." }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$name]) { Stop-Install "SHA-256 mismatch for release/$name." }
}
if ($VerifyOnly) {
    Write-Host 'Offline firmware assets: SHA-256 verified.'
    exit 0
}

foreach ($tool in @('adb','tar')) {
    if (-not (Get-Command $tool -CommandType Application -ErrorAction SilentlyContinue)) {
        Stop-Install "required host tool '$tool' is missing. Install it before running the offline package."
    }
}
$python = $null
foreach ($name in @('python','python3','py')) {
    $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) {
        $probe = & $candidate.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate.Source; break }
    }
}
if (-not $python) { Stop-Install 'Python 3.8 or newer is required; install it before running the offline package.' }
& $python -c 'import lz4.block' 2>$null
if ($LASTEXITCODE -ne 0) { Stop-Install 'the Python lz4 module is required; install it before running the offline package. This entry point will not use pip.' }

$invokeArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Package 'source\install.ps1'),
    '-U30AirHostPackage','-NoSelfUpdate','-ReleaseDirectory',$Release,'-Lang',$Lang)
if ($Check) { $invokeArgs += '-Check' }
if ($Work) { $invokeArgs += @('-Work',$Work) }
& powershell.exe @invokeArgs
exit $LASTEXITCODE
