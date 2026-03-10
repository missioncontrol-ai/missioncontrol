param(
    [string]$Endpoint = "",
    [string]$Token = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command missioncontrol-mcp -ErrorAction SilentlyContinue)) {
    throw "[FAIL] missioncontrol-mcp not found on PATH"
}

Write-Host "[OK] missioncontrol-mcp found"

try {
    & missioncontrol-mcp --help | Out-Null
    Write-Host "[OK] missioncontrol-mcp --help"
}
catch {
    Write-Warning "[WARN] missioncontrol-mcp exists but --help failed"
}

if ($Endpoint) {
    $previousBaseUrl = $env:MC_BASE_URL
    $previousToken = $env:MC_TOKEN
    $env:MC_BASE_URL = $Endpoint
    $env:MC_TOKEN = $Token
    try {
        $doctorRaw = & missioncontrol-mcp doctor | Out-String
        if (-not $doctorRaw) {
            Write-Warning "[WARN] missioncontrol-mcp doctor returned no output"
        }
        else {
            $doctorJson = $doctorRaw | ConvertFrom-Json
            if ($doctorJson.checks.$Endpoint.health_ok -eq $true) {
                Write-Host "[OK] missioncontrol-mcp doctor health check"
            }
            else {
                Write-Warning "[WARN] missioncontrol-mcp doctor reports health check failure"
            }
            if ($Token) {
                if ($doctorJson.checks.$Endpoint.tools_ok -eq $true) {
                    Write-Host "[OK] missioncontrol-mcp doctor tools check"
                }
                else {
                    Write-Warning "[WARN] missioncontrol-mcp doctor reports tools check failure"
                }
            }
        }
    }
    catch {
        Write-Warning "[WARN] missioncontrol-mcp doctor command failed"
    }
    finally {
        $env:MC_BASE_URL = $previousBaseUrl
        $env:MC_TOKEN = $previousToken
    }
}

if (-not $Endpoint) {
    Write-Host "[INFO] No endpoint set. Local bootstrap is complete; set MC_BASE_URL to connect."
    exit 0
}

if ($Endpoint -notmatch '^https?://') {
    Write-Warning "[WARN] Endpoint does not start with http:// or https:// : $Endpoint"
    exit 0
}

try {
    Invoke-WebRequest -UseBasicParsing -Method GET -Uri "$Endpoint/" -TimeoutSec 8 | Out-Null
    Write-Host "[OK] endpoint reachable: $Endpoint"
}
catch {
    Write-Warning "[WARN] endpoint not reachable: $Endpoint"
}

if ($Token) {
    try {
        $headers = @{ Authorization = "Bearer $Token" }
        Invoke-WebRequest -UseBasicParsing -Method GET -Uri "$Endpoint/mcp/health" -Headers $headers -TimeoutSec 8 | Out-Null
        Write-Host "[OK] authenticated /mcp/health"
    }
    catch {
        Write-Warning "[WARN] /mcp/health check failed (token invalid, auth policy, or connectivity)."
    }
}
else {
    Write-Host "[INFO] No token provided; skipping authenticated /mcp/health check."
}

if (Get-Command missioncontrol-explorer -ErrorAction SilentlyContinue) {
    if ($Endpoint) {
        $previousBaseUrl = $env:MC_BASE_URL
        $previousToken = $env:MC_TOKEN
        $env:MC_BASE_URL = $Endpoint
        $env:MC_TOKEN = $Token
        try {
            $explorerRaw = & missioncontrol-explorer tree --format json 2>$null | Out-String
            if (-not $explorerRaw) {
                Write-Warning "[WARN] missioncontrol-explorer returned no output"
            }
            else {
                $explorerJson = $explorerRaw | ConvertFrom-Json
                if ($null -ne $explorerJson.mission_count) {
                    Write-Host "[OK] missioncontrol-explorer tree --format json"
                }
                else {
                    Write-Warning "[WARN] missioncontrol-explorer returned unexpected JSON shape"
                }
            }
        }
        catch {
            Write-Warning "[WARN] missioncontrol-explorer failed"
        }
        finally {
            $env:MC_BASE_URL = $previousBaseUrl
            $env:MC_TOKEN = $previousToken
        }
    }
    else {
        Write-Host "[INFO] missioncontrol-explorer found; skipping explorer run because endpoint is empty"
    }
}
else {
    Write-Warning "[WARN] missioncontrol-explorer not found on PATH"
}

Write-Host "[DONE] doctor checks finished"
