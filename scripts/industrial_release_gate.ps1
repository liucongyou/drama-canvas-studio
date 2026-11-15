param(
  [string]$DatabaseUrl = 'postgresql://postgres@127.0.0.1:55432/drama_canvas_snapshot',
  [string]$CanvasBase = 'http://127.0.0.1:8890',
  [string]$ReportPath = 'artifacts/release-gate.json'
)

$ErrorActionPreference = 'Stop'
$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Workspace '.venv\Scripts\python.exe'
$Web = Join-Path $Workspace 'web-vimax'
$Report = [ordered]@{
  commit = (& git -C $Workspace rev-parse HEAD)
  backendTests = 'pending'
  agentTests = 'pending'
  frontendTests = 'pending'
  typecheck = 'pending'
  build = 'pending'
  migrationIdempotent = $false
  stress = 'pending'
  multiprocessStress = 'pending'
  browser = 'pending'
  gatePassed = $false
}

function Invoke-Step([string]$Name, [scriptblock]$Command) {
  Write-Host "[release-gate] $Name"
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE"
  }
}

try {
  Push-Location $Workspace
  Invoke-Step 'backendTests' {
    & $Python -X utf8 scripts\run_backend_tests.py
  }
  $Report.backendTests = 'pass'

  $AgentPython = Join-Path $Workspace 'agent\.venv\Scripts\python.exe'
  if (-not (Test-Path $AgentPython)) {
    throw "Agent virtualenv is missing: $AgentPython"
  }
  Invoke-Step 'agentTests' {
    Push-Location (Join-Path $Workspace 'agent')
    $PreviousTestPostgresUrl = $env:TEST_POSTGRES_URL
    try {
      $env:TEST_POSTGRES_URL = $DatabaseUrl
      & $AgentPython -m pytest tests -q
      if ($LASTEXITCODE -ne 0) {
        throw "agentTests failed with exit code $LASTEXITCODE"
      }
    } finally {
      $env:TEST_POSTGRES_URL = $PreviousTestPostgresUrl
      Pop-Location
    }
  }
  $Report.agentTests = 'pass'

  Push-Location $Web
  Invoke-Step 'typecheck' { npm run typecheck:canvas }
  $Report.typecheck = 'pass'
  Invoke-Step 'frontendTests' { npm test }
  $Report.frontendTests = 'pass'
  Invoke-Step 'build' { npm run build }
  $Report.build = 'pass'
  Pop-Location

  $FirstReport = Join-Path $Workspace 'artifacts\migration-first.json'
  $SecondReport = Join-Path $Workspace 'artifacts\migration-second.json'
  Invoke-Step 'migration-first' {
    & $Python -X utf8 scripts\run-local-migrations.py `
      --url $DatabaseUrl `
      --report $FirstReport
  }
  Invoke-Step 'migration-second' {
    & $Python -X utf8 scripts\run-local-migrations.py `
      --url $DatabaseUrl `
      --report $SecondReport
  }
  $Second = Get-Content -Raw $SecondReport | ConvertFrom-Json
  if ([int]$Second.changedRows -ne 0) {
    throw "Second migration changed $($Second.changedRows) rows"
  }
  $Report.migrationIdempotent = $true

  Invoke-Step 'stress' {
    & $Python -X utf8 scripts\industrial_stress.py `
      --url $DatabaseUrl `
      --report (Join-Path $Workspace 'artifacts\industrial-stress.json')
  }
  $Stress = Get-Content -Raw (
    Join-Path $Workspace 'artifacts\industrial-stress.json'
  ) | ConvertFrom-Json
  if (-not $Stress.passed) { throw 'Stress report did not pass' }
  $Report.stress = 'pass'

  $MultiProcessOutput = Join-Path $Workspace (
    "artifacts\multiprocess-gate-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
  )
  Invoke-Step 'multiprocessSmoke' {
    & $Python -X utf8 scripts\multiprocess_stress.py `
      --url $DatabaseUrl `
      --tenants 1 `
      --tasks-per-tenant 1 `
      --workers 2 `
      --worker-concurrency 1 `
      --lease-seconds 15 `
      --provider-delay 0.5 `
      --timeout-seconds 60 `
      --output $MultiProcessOutput
  }
  $Report.multiprocessStress = 'pass'

  if (-not $env:CANVAS_PASSWORD) {
    throw 'CANVAS_PASSWORD is required for the industrial browser gate'
  }
  $env:CANVAS_TENANT_DATA_ROOT = Join-Path $Workspace 'data\tenants'
  Invoke-Step 'browserFixture' {
    & $Python -X utf8 scripts\seed-browser-fixture.py `
      --url $DatabaseUrl `
      --count 60 `
      --tenant owner | Out-Null
  }
  $env:CANVAS_BASE = $CanvasBase
  $env:CANVAS_USERNAME = if ($env:CANVAS_USERNAME) {
    $env:CANVAS_USERNAME
  } else {
    'admin'
  }
  $env:PROJECT_ID = if ($env:PROJECT_ID) {
    $env:PROJECT_ID
  } else {
    'JWDLEgTbbaF2t7glAVUbqY'
  }
  $env:EPISODE_ID = if ($env:EPISODE_ID) {
    $env:EPISODE_ID
  } else {
    'EP001'
  }
  $env:MIN_MEDIA_NODES = if ($env:MIN_MEDIA_NODES) {
    $env:MIN_MEDIA_NODES
  } else {
    '50'
  }
  Invoke-Step 'browser' {
    node web-vimax\scripts\verify-industrial-canvas.mjs
  }
  $Report.browser = 'pass'

  Invoke-Step 'git-diff-check' {
    & git -C $Workspace diff --check
  }
  $Report.gatePassed = $true
} finally {
  while ((Get-Location).Path -ne $Workspace) {
    Pop-Location
  }
  $Target = Join-Path $Workspace $ReportPath
  New-Item -ItemType Directory -Force (Split-Path $Target) | Out-Null
  $Report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Target -Encoding utf8
}

if (-not $Report.gatePassed) { exit 1 }
Write-Output "Release gate passed: $($Report.commit)"
