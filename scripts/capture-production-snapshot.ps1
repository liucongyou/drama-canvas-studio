param(
  [string]$HostName = '121.196.230.104',
  [string]$UserName = 'root',
  [string]$RemoteEnvFile = '/etc/drama-canvas-pg.env',
  [string]$OutputDirectory = 'artifacts'
)

$ErrorActionPreference = 'Stop'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$Dump = Join-Path $OutputDirectory "prod-snapshot-$Stamp.dump"
$RemoteDump = "/tmp/drama-canvas-prod-$Stamp.dump"
$Target = "$UserName@$HostName"

ssh $Target "set -a; . '$RemoteEnvFile'; set +a; export PATH=/www/server/pgsql/bin:`$PATH; pg_dump --format=custom --no-owner --no-acl --file='$RemoteDump' `"`$DATABASE_URL`""
if ($LASTEXITCODE -ne 0) {
  throw "Remote pg_dump failed with exit code $LASTEXITCODE"
}

scp -q "${Target}:$RemoteDump" $Dump
if ($LASTEXITCODE -ne 0) {
  throw "scp failed with exit code $LASTEXITCODE"
}

ssh $Target "rm -f '$RemoteDump'"
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Dump).Hash.ToLowerInvariant()
$Manifest = [ordered]@{
  createdAt = (Get-Date).ToUniversalTime().ToString('o')
  host = $HostName
  dump = [System.IO.Path]::GetFileName($Dump)
  sha256 = $Hash
  bytes = (Get-Item -LiteralPath $Dump).Length
}
$ManifestPath = Join-Path $OutputDirectory "prod-snapshot-$Stamp.manifest.json"
$Manifest | ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding utf8
Write-Output $Dump
Write-Output $ManifestPath
