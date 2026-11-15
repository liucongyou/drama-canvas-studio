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
    try {
      & $AgentPython -m pytest tests -q
      if ($LASTEXITCODE -ne 0) {
        throw "agentTests failed with exit code $LASTEXITCODE"
      }
    } finally {
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

  Invoke-Step 'browser' {
    $env:CANVAS_BASE = $CanvasBase
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
