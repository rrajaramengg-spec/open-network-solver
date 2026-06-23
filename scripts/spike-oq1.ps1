#!/usr/bin/env pwsh
# scripts/spike-oq1.ps1
#
# Phase 1 task 1.8 — OQ-1 spike runner.
#
# This is a thin wrapper around the docker compose ETL invocation that
# captures wall-clock + peak container memory for each stage and appends a
# row to docs/phases/phase-1-foundation.md "Results table".
#
# Usage (from repo root):
#   pwsh scripts/spike-oq1.ps1 -PbfPath C:\Data\us-west-260619.osm.pbf -Label us-west
#   pwsh scripts/spike-oq1.ps1 -PbfPath /data/osm/nevada.osm.pbf -Label nevada -InContainer
#
# Captures:
#   * wall-clock per stage (from the ETL's JSON logs)
#   * peak memory (from `docker stats --no-stream` polled every 2s during the run)

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $PbfPath,

    [Parameter(Mandatory = $true)]
    [string] $Label,

    [string] $ComposeFile = "infra/docker-compose.yml",
    [string] $EnvFile = "infra/.env",
    [switch] $InContainer
)

$ErrorActionPreference = "Stop"

if (-not $InContainer -and -not (Test-Path $PbfPath)) {
    throw "PBF not found on host: $PbfPath"
}

# All operation logs live under <repo-root>/logs/ (gitignored). Anchor to the
# script location so the output lands there regardless of the caller's cwd.
$repoRoot = (Resolve-Path -Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "spike-$Label.log"
$statsFile = Join-Path $logDir "spike-$Label.stats"
Write-Host "[spike] label=$Label  pbf=$PbfPath  log=$logFile  stats=$statsFile"

# Start a background process polling docker stats every 2 seconds for the
# postgres-routing-primary container (which is where the actual osm2pgrouting
# memory pressure lives — the ETL container itself does mostly stdin parsing).
$statsJob = Start-Job -ScriptBlock {
    param($statsFile)
    while ($true) {
        try {
            $line = docker stats ons-postgres-primary ons-etl `
                --no-stream `
                --format "{{.Name}},{{.MemUsage}},{{.MemPerc}},{{.CPUPerc}}" 2>$null
            if ($line) {
                "$(Get-Date -Format o),$line" | Out-File -FilePath $statsFile -Append -Encoding utf8
            }
        }
        catch {}
        Start-Sleep -Seconds 2
    }
} -ArgumentList $statsFile

$started = Get-Date
try {
    # Determine the path to use inside the container. If the caller passed a
    # host path, copy into the named volume first.
    $inContainerPath = if ($InContainer) {
        $PbfPath
    } else {
        $volName = "open-network-solver_osm-data"
        Write-Host "[spike] staging PBF into volume $volName ..."
        docker run --rm -v "${volName}:/dst" -v "$($PbfPath | Split-Path -Parent):/src:ro" `
            alpine cp -v "/src/$($PbfPath | Split-Path -Leaf)" "/dst/"
        "/data/osm/$($PbfPath | Split-Path -Leaf)"
    }

    Write-Host "[spike] running ETL: pbf=$inContainerPath"
    docker compose --env-file $EnvFile -f $ComposeFile --profile etl `
        run --rm etl --pbf $inContainerPath *> $logFile
    $etlExit = $LASTEXITCODE
}
finally {
    Stop-Job $statsJob -ErrorAction SilentlyContinue
    Remove-Job $statsJob -Force -ErrorAction SilentlyContinue
}

$ended = Get-Date
$totalSec = ($ended - $started).TotalSeconds

# Parse peak memory from the stats file (rows look like:
#   2026-06-20T22:15:00,ons-postgres-primary,1.2GiB / 16GiB,7.5%,42.0%
$peakMem = "n/a"
if (Test-Path $statsFile) {
    $maxBytes = 0
    Get-Content $statsFile | ForEach-Object {
        if ($_ -match "ons-postgres-primary,([\d\.]+)(KiB|MiB|GiB)") {
            $v = [double]$Matches[1]
            $unit = $Matches[2]
            $bytes = switch ($unit) {
                "KiB" { $v * 1KB }
                "MiB" { $v * 1MB }
                "GiB" { $v * 1GB }
            }
            if ($bytes -gt $maxBytes) { $maxBytes = $bytes }
        }
    }
    if ($maxBytes -gt 0) {
        $peakMem = "{0:N2} GiB" -f ($maxBytes / 1GB)
    }
}

Write-Host ""
Write-Host "[spike] label=$Label  exit=$etlExit  total=${totalSec}s  peak_postgres_mem=$peakMem"
Write-Host "[spike] full log:   $logFile"
Write-Host "[spike] stats raw:  $statsFile"
Write-Host ""
Write-Host "Append this row to docs/phases/phase-1-foundation.md > 'Results table':"
Write-Host "| $Label | end-to-end | $([Math]::Round($totalSec,1))s | $peakMem | exit=$etlExit |"

exit $etlExit
