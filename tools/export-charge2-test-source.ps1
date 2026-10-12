[CmdletBinding()]
param(
    [string]$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [Parameter(Mandatory = $true)][string]$OutDir
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$git = Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1

$status = & $git.Source -C $Repo status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the source worktree.' }
if ($status) { throw "Source worktree is dirty; commit all source changes first:`n$($status -join "`n")" }
$commit = (& $git.Source -C $Repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'Could not identify the source commit.' }
$epochText = (& $git.Source -C $Repo show -s --format=%ct $commit).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not read the source commit timestamp.' }
$epoch = [int64]$epochText

$OutDir = [IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$archive = Join-Path $OutDir 'source.snapshot.tar'
$metadataPath = Join-Path $OutDir 'source.snapshot.json'
if ((Test-Path -LiteralPath $archive) -or (Test-Path -LiteralPath $metadataPath)) {
    throw "Source export output already exists under $OutDir. Choose a new directory."
}
& $git.Source -C $Repo archive --format=tar --output=$archive $commit
if ($LASTEXITCODE -ne 0) { throw 'git archive failed.' }
$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
$metadata = [ordered]@{
    source_commit = $commit
    source_date_epoch = $epoch
    source_snapshot_sha256 = $archiveHash
    source_worktree_clean = $true
}
[IO.File]::WriteAllText($metadataPath, ($metadata | ConvertTo-Json -Depth 4) + "`n", [Text.UTF8Encoding]::new($false))
Write-Output "source commit: $commit"
Write-Output "source archive SHA-256: $archiveHash"
Write-Output "archive: $archive"
Write-Output "metadata: $metadataPath"
