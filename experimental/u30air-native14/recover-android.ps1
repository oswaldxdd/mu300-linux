[CmdletBinding()]
param([string]$BackupPath = '', [string]$LinuxOffset = '')
$ErrorActionPreference = 'Stop'
$adb = Join-Path $PSScriptRoot 'adb.exe'
if (-not (Test-Path -LiteralPath $adb)) { throw 'Bundled adb.exe is missing.' }
if (-not $BackupPath) { $BackupPath = Read-Host 'Paste the Rollback snapshot path printed during installation' }
if ($BackupPath -notmatch '^/mnt/mu300-disk/\.mu300-native12-rollback/(\d{8}T\d{6}Z-\d+)$') { throw 'Invalid snapshot path.' }
$snapshot = $Matches[1]
if (-not $LinuxOffset) { $LinuxOffset = Read-Host 'Enter the Android recovery Linux offset printed during installation' }
if ($LinuxOffset -notmatch '^\d+$' -or $LinuxOffset -eq '0') { throw 'Invalid Linux offset.' }
$devices = @(& $adb devices | Where-Object { $_ -match '^\S+\s+device$' })
if ($devices.Count -ne 1) { throw 'Connect exactly one U30 Air in rooted Android A, enable USB debugging and authorize the computer.' }
& $adb shell su -c id
if ($LASTEXITCODE -ne 0) { throw 'Magisk su is unavailable or not authorized.' }
& $adb shell mkdir -p /data/local/tmp/mu300-native-recovery
if ($LASTEXITCODE -ne 0) { throw 'Could not create recovery transfer directory.' }
foreach ($name in @('recover-android.sh', 'rollback-device.sh', 'android-mount-mu300root.sh', 'busybox')) {
    & $adb push (Join-Path $PSScriptRoot $name) /data/local/tmp/mu300-native-recovery/
    if ($LASTEXITCODE -ne 0) { throw "Could not upload $name" }
}
& $adb shell "su -c 'chmod 755 /data/local/tmp/mu300-native-recovery/busybox'"
if ($LASTEXITCODE -ne 0) { throw 'Could not enable recovery busybox.' }
$command = "sh /data/local/tmp/mu300-native-recovery/recover-android.sh $snapshot $LinuxOffset"
& $adb shell "su -c '$command'"
if ($LASTEXITCODE -ne 0) { throw 'Recovery failed. Keep the on-device snapshot; do not format the Linux disk.' }
Write-Host 'Saved OpenWrt and boot image restored. No reboot was performed.' -ForegroundColor Green
