param(
  [Parameter(Mandatory = $true)]
  [string]$Dump,
  [string]$Database = 'drama_canvas_snapshot',
  [string]$PgHost = '127.0.0.1',
  [int]$Port = 55432,
  [string]$User = 'postgres'
)

$ErrorActionPreference = 'Stop'
$PgBin = 'C:\Program Files\PostgreSQL\16\bin'
$Dump = [System.IO.Path]::GetFullPath($Dump)
if (-not (Test-Path -LiteralPath $Dump)) {
  throw "Snapshot dump not found: $Dump"
}

& "$PgBin\dropdb.exe" -h $PgHost -p $Port -U $User --if-exists $Database
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$PgBin\createdb.exe" -h $PgHost -p $Port -U $User $Database
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$PgBin\pg_restore.exe" `
  -h $PgHost `
  -p $Port `
  -U $User `
  --no-owner `
  --no-acl `
  --exit-on-error `
  --dbname $Database `
  $Dump
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Restored $Dump into $Database"
