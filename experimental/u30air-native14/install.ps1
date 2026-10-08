[CmdletBinding()]
param(
    [string]$DeviceIp = '192.168.78.1',
    [string]$KnownHostsFile = (Join-Path $PSScriptRoot 'device_known_hosts'),
    [switch]$Check,
    [switch]$Preflight
)
$ErrorActionPreference = 'Stop'
if ($DeviceIp -notmatch '^\d{1,3}(?:\.\d{1,3}){3}$') { throw 'Use a literal IPv4 device address.' }
$parsed = $null
if (-not [Net.IPAddress]::TryParse($DeviceIp, [ref]$parsed)) { throw 'Invalid IPv4 address.' }
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'delivery.json') -Raw | ConvertFrom-Json
$archive = Join-Path $PSScriptRoot $manifest.archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) {
    throw 'Firmware archive checksum mismatch.'
}
if ($Check) { Write-Host 'Offline package verification passed; no device connection.'; exit 0 }
$ssh = (Get-Command ssh.exe -CommandType Application -ErrorAction Stop).Source
$scp = (Get-Command scp.exe -CommandType Application -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $KnownHostsFile -PathType Leaf)) { throw "Pinned device key file not found: $KnownHostsFile" }
# Use the separately captured, device-confirmed key; don't consult or change global known_hosts.
$sshOptions = @('-o','StrictHostKeyChecking=yes','-o',"UserKnownHostsFile=$KnownHostsFile",'-o','GlobalKnownHostsFile=NUL','-o','ConnectTimeout=15','-o','ServerAliveInterval=15')
$target = "root@$DeviceIp"
if ($Preflight) {
    & $scp -O @sshOptions (Join-Path $PSScriptRoot 'collect-status.sh') "${target}:/tmp/u30-native14-status.sh"
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic script transfer failed.' }
    & $ssh @sshOptions $target 'sh /tmp/u30-native14-status.sh'
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic command failed.' }
    exit 0
}
$stamp = [Guid]::NewGuid().ToString('N')
$stage = "/mnt/mu300-disk/.u30-native14-upload-$stamp"
& $ssh @sshOptions $target "test -d /mnt/mu300-disk/openwrt && mkdir -m 700 '$stage'"
if ($LASTEXITCODE -ne 0) { throw 'Cannot create device staging directory.' }
& $scp -O @sshOptions $archive "${target}:$stage/firmware.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "Transfer failed. Incomplete staging directory: $stage" }
$command = "set -eu; cd '$stage'; echo '$($manifest.sha256)  firmware.tar.gz' | sha256sum -c -; tar -xzf firmware.tar.gz; sh package/install-device.sh"
& $ssh -tt @sshOptions $target $command
if ($LASTEXITCODE -ne 0) { throw 'Device installation failed; keep the full console output and rollback path.' }
Write-Host 'Installation completed. Save the rollback path; reboot is a separate step.'
