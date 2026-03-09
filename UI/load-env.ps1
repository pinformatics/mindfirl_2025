param(
    [string]$EnvFile = ".env",
    [switch]$NoClear
)

$managedVars = @(
    "FLASK_SECRET_KEY",
    "ADMIN_PASSWORD",
    "REDIS_URL",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_USERNAME",
    "REDIS_PASSWORD",
    "REDIS_USE_TLS",
    "PORT"
)

if (-not $NoClear) {
    foreach ($name in $managedVars) {
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -Path $EnvFile)) {
    Write-Error "Env file not found: $EnvFile"
    exit 1
}

Get-Content -Path $EnvFile | ForEach-Object {
    $line = $_.Trim()

    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
        return
    }

    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) {
        return
    }

    $name = $parts[0].Trim()
    $value = $parts[1].Trim()

    if ($name) {
        Set-Item -Path "Env:$name" -Value $value
    }
}

Write-Host "Loaded environment variables from $EnvFile"
if (-not $NoClear) {
    Write-Host "Cleared existing app env vars before loading (use -NoClear to keep current vars)."
}
