[CmdletBinding()]
param([string]$DeviceIp='192.168.78.1', [string]$BackupPath='')
$ErrorActionPreference='Stop'
if ($DeviceIp -notmatch '^\d{1,3}(?:\.\d{1,3}){3}$') { throw 'Use a literal IPv4 address.' }
if (-not $BackupPath) { $BackupPath=Read-Host 'Paste the Rollback snapshot path from installation' }
if ($BackupPath -notmatch '^/mnt/mu300-disk/\.mu300-native2-rollback/\d{8}T\d{6}Z-\d+$') { throw 'Invalid snapshot path.' }
& ssh.exe -tt -o StrictHostKeyChecking=ask -o ConnectTimeout=15 "root@$DeviceIp" "sh '$BackupPath/rollback.sh' '$BackupPath'"
if ($LASTEXITCODE -ne 0) { throw 'Rollback failed; preserve the snapshot and console output.' }
