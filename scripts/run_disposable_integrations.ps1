[CmdletBinding()]
param(
    [string]$PythonExecutable = "python",
    [string]$ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$reportTarget = $null
if (![string]::IsNullOrWhiteSpace($ReportPath)) {
    $reportTarget = [IO.Path]::GetFullPath($ReportPath)
}
Push-Location $projectRoot

# Disposable integration tests must never reuse a developer or production
# encryption key. Generate an in-memory 256-bit key for this process and put
# the caller's value back in the environment when the runner exits.
$previousMasterKey = [Environment]::GetEnvironmentVariable("PAST_PARTNER_MASTER_KEY", "Process")
$temporaryMasterKeyBytes = New-Object byte[] 32
$randomNumberGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomNumberGenerator.GetBytes($temporaryMasterKeyBytes)
}
finally {
    $randomNumberGenerator.Dispose()
}
$env:PAST_PARTNER_MASTER_KEY = [Convert]::ToBase64String($temporaryMasterKeyBytes)

function ConvertTo-SafeOutput {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrEmpty($Value)) {
        return ""
    }

    $safe = $Value
    foreach ($name in @(
        "PAST_PARTNER_MASTER_KEY",
        "PAST_PARTNER_METADATA_DSN",
        "PAST_PARTNER_S3_TEST_ENDPOINT",
        "PAST_PARTNER_S3_TEST_ACCESS_KEY",
        "PAST_PARTNER_S3_TEST_SECRET_KEY",
        "PAST_PARTNER_S3_TEST_SESSION_TOKEN",
        "PAST_PARTNER_KMS_TEST_ACCESS_KEY",
        "PAST_PARTNER_KMS_TEST_SECRET_KEY",
        "PAST_PARTNER_KMS_TEST_SESSION_TOKEN",
        "PAST_PARTNER_KMS_TEST_ENDPOINT"
    )) {
        $secret = [Environment]::GetEnvironmentVariable($name)
        if (![string]::IsNullOrEmpty($secret)) {
            $safe = $safe.Replace($secret, "<redacted>")
        }
    }

    # Keep connection errors useful without printing user/password/query data.
    $safe = [regex]::Replace($safe, "(?i)(postgres(?:ql)?://)[^\s@]+@", '$1<redacted>@')
    $safe = [regex]::Replace($safe, "(?i)(https?://)[^\s/]+:[^\s/@]+@", '$1<redacted>@')
    return $safe
}

function Require-EnvironmentValue {
    param([Parameter(Mandatory)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required disposable setting is missing: $Name"
    }
}

function Assert-DisposableConfiguration {
    if ([Environment]::GetEnvironmentVariable("PAST_PARTNER_DISPOSABLE_RUN") -ne "1") {
        throw "Set PAST_PARTNER_DISPOSABLE_RUN=1 to acknowledge destructive disposable integration tests."
    }

    foreach ($name in @(
        "PAST_PARTNER_METADATA_DSN",
        "PAST_PARTNER_S3_TEST_ENDPOINT",
        "PAST_PARTNER_S3_TEST_BUCKET",
        "PAST_PARTNER_S3_TEST_ACCESS_KEY",
        "PAST_PARTNER_S3_TEST_SECRET_KEY",
        "PAST_PARTNER_KMS_TEST_ENDPOINT",
        "PAST_PARTNER_KMS_TEST_KEY_ID",
        "PAST_PARTNER_KMS_TEST_ACCESS_KEY",
        "PAST_PARTNER_KMS_TEST_SECRET_KEY"
    )) {
        Require-EnvironmentValue -Name $name
    }

    foreach ($name in @(
        "PAST_PARTNER_METADATA_TEST_DISPOSABLE",
        "PAST_PARTNER_S3_TEST_DISPOSABLE",
        "PAST_PARTNER_KMS_TEST_DISPOSABLE"
    )) {
        if ([Environment]::GetEnvironmentVariable($name) -ne "1") {
            throw "Set $name=1; the runner refuses non-disposable resources."
        }
    }
}

function Invoke-IntegrationModule {
    param([Parameter(Mandatory)][string]$Module)

    Write-Host "[R0-01] running $Module"
    $started = Get-Date
    try {
        $raw = (& $PythonExecutable -m unittest $Module -v 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
        $safe = ConvertTo-SafeOutput $raw
        Write-Host $safe.TrimEnd()
        $hasSkippedTests = $raw -match "(?im)^\s*(?:OK|FAILED) \(.*skipped=\d+"
        $status = if ($exitCode -eq 0 -and !$hasSkippedTests) { "passed" } else { "failed" }
        $failureCode = if ($hasSkippedTests) { "skipped_tests" } elseif ($exitCode -ne 0) { "module_failed" } else { $null }
        if ($hasSkippedTests) {
            Write-Host "[R0-01] failed ${Module}: one or more tests were skipped; real disposable verification requires zero skips."
        }
        Write-Host "[R0-01] $status $Module"
        return [ordered]@{
            module = $Module
            status = $status
            exit_code = $exitCode
            failure_code = $failureCode
            duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        }
    }
    catch {
        $safeError = ConvertTo-SafeOutput $_.Exception.Message
        Write-Error "[R0-01] failed ${Module}: $safeError" -ErrorAction Continue
        return [ordered]@{
            module = $Module
            status = "failed"
            exit_code = 1
            failure_code = "runner_process_failed"
            duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        }
    }
}

$modules = @(
    "tests.integration.test_postgresql_metadata_store",
    "tests.integration.test_s3_blob_store",
    "tests.integration.test_kms_master_key",
    "tests.integration.test_task_queue_backends"
)
$results = @()
$runStatus = "failed"
$failureCode = $null
$failedModule = $null

try {
    Assert-DisposableConfiguration
    Write-Host "[R0-01] disposable configuration accepted; external resources must be empty after teardown."
    foreach ($module in $modules) {
        $result = Invoke-IntegrationModule -Module $module
        $results += $result
        if ($result.status -ne "passed") {
            $failedModule = $module
            $failureCode = $result.failure_code
            throw "Disposable integration module failed: $module"
        }
    }
    $runStatus = "passed"
    Write-Host "[R0-01] success: all disposable integration contracts passed and their test-owned resources were torn down."
}
catch {
    $safeError = ConvertTo-SafeOutput $_.Exception.Message
    if ($null -eq $failureCode) {
        $failureCode = "configuration_rejected"
    }
    Write-Error "[R0-01] failure: $safeError"
    $runStatus = "failed"
}
finally {
    if ($null -eq $previousMasterKey) {
        Remove-Item Env:PAST_PARTNER_MASTER_KEY -ErrorAction SilentlyContinue
    }
    else {
        $env:PAST_PARTNER_MASTER_KEY = $previousMasterKey
    }
    Pop-Location
    if ($null -ne $reportTarget) {
        $reportParent = Split-Path -Parent $reportTarget
        if (![string]::IsNullOrWhiteSpace($reportParent)) {
            New-Item -ItemType Directory -Force -Path $reportParent | Out-Null
        }
        $report = [ordered]@{
            status = $runStatus
            failure_code = $failureCode
            failed_module = $failedModule
            results = $results
            resources = "test-owned resources are deleted by each integration fixture; external KMS key lifecycle remains caller-owned"
        }
        $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportTarget -Encoding utf8
    }
}

if ($runStatus -ne "passed") {
    exit 1
}
