param(
  [ValidateSet('init', 'start', 'stop', 'status', 'reset')]
  [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Root = Join-Path $Workspace '.pg-test'
$OfficialPortableBin = Join-Path $Root 'pgsql\bin'
$WheelPortableBin = Join-Path $Root 'pgserver\pgserver\pginstall\bin'
$PortableBin = if (Test-Path (Join-Path $OfficialPortableBin 'initdb.exe')) {
  $OfficialPortableBin
} else {
  $WheelPortableBin
}
$Archive = Join-Path $Root 'postgresql-16.16-binaries.zip'
$Wheel = Join-Path $Root 'pgserver.whl'
if (-not (Test-Path (Join-Path $WheelPortableBin 'initdb.exe')) -and (Test-Path $Wheel)) {
  New-Item -ItemType Directory -Force (Join-Path $Root 'pgserver') | Out-Null
  & tar.exe -xf $Wheel -C (Join-Path $Root 'pgserver')
}
if (-not (Test-Path (Join-Path $OfficialPortableBin 'initdb.exe')) -and (Test-Path $Archive) -and ((Get-Item $Archive).Length -gt 300MB)) {
  Expand-Archive -LiteralPath $Archive -DestinationPath $Root -Force
}
$PgBin = if (Test-Path (Join-Path $OfficialPortableBin 'initdb.exe')) {
  $OfficialPortableBin
} elseif (Test-Path (Join-Path $WheelPortableBin 'initdb.exe')) {
  $WheelPortableBin
} else {
  'C:\Program Files\PostgreSQL\16\bin'
}
$Data = Join-Path $Root 'data'
$Log = Join-Path $Root 'postgres.log'
$Port = 55432

if (-not (Test-Path $PgBin)) {
  throw "PostgreSQL 16 bin directory is missing: $PgBin"
}

switch ($Action) {
  'init' {
    if (Test-Path $Data) {
      throw "Data directory already exists: $Data"
    }
    New-Item -ItemType Directory -Force $Root | Out-Null
    & "$PgBin\initdb.exe" `
      -D $Data `
      -U postgres `
      -A trust `
      --encoding=UTF8
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
  'start' {
    if (-not (Test-Path $Data)) {
      throw "Data directory is missing. Run '$PSCommandPath init' first."
    }
    & "$PgBin\pg_ctl.exe" `
      -D $Data `
      -l $Log `
      -t 180 `
      -o "-p $Port -h 127.0.0.1" `
      start
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
  'stop' {
    if (-not (Test-Path $Data)) {
      throw "Data directory is missing: $Data"
    }
    & "$PgBin\pg_ctl.exe" -D $Data stop -m fast
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
  'status' {
    if (-not (Test-Path $Data)) {
      Write-Output "NOT_INITIALIZED $Data"
      exit 1
    }
    & "$PgBin\pg_ctl.exe" -D $Data status
    exit $LASTEXITCODE
  }
  'reset' {
    if (Test-Path $Data) {
      $ResolvedData = (Resolve-Path $Data).Path
      if (-not $ResolvedData.StartsWith(
        $Workspace,
        [StringComparison]::OrdinalIgnoreCase
      )) {
        throw "Refusing to delete outside workspace: $ResolvedData"
      }
      if (Test-Path (Join-Path $Data 'postmaster.pid')) {
        & "$PgBin\pg_ctl.exe" -D $Data stop -m fast
      }
      Remove-Item -LiteralPath $Data -Recurse -Force
    }
    & $PSCommandPath init
  }
}
