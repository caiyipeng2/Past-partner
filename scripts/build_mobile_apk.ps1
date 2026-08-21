[CmdletBinding()]
param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..'),
    [string]$OutputDirectory = 'E:\Tools',
    [string]$FlutterExecutable = 'flutter',
    [switch]$SkipBuild,
    [switch]$StoreRelease
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-MobileVersion {
    param([Parameter(Mandatory)][string]$PubspecPath)

    $content = Get-Content -LiteralPath $PubspecPath -Raw
    $match = [regex]::Match($content, '(?m)^\s*version:\s*([^\s]+)\s*$')
    if (!$match.Success) {
        throw "Unable to read mobile version from pubspec.yaml."
    }
    return $match.Groups[1].Value
}

function Get-MobileApkName {
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][datetime]$Timestamp,
        [Parameter(Mandatory)][ValidateSet('debug', 'release')][string]$Kind
    )

    $safeVersion = $Version -replace '[^0-9A-Za-z.+-]', '_'
    return "Past-partner_${safeVersion}_$($Timestamp.ToString('yyyyMMdd_HHmm'))_${Kind}.apk"
}

function Remove-MobileApkOutputs {
    param([Parameter(Mandatory)][string]$Directory)

    if (!(Test-Path -LiteralPath $Directory)) {
        New-Item -ItemType Directory -Path $Directory | Out-Null
    }
    Get-ChildItem -LiteralPath $Directory -File -Filter 'Past-partner_*.apk' -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

function Assert-StoreReleaseEnvironment {
    $required = @(
        'PAST_PARTNER_ANDROID_KEYSTORE_FILE',
        'PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD',
        'PAST_PARTNER_ANDROID_KEY_ALIAS',
        'PAST_PARTNER_ANDROID_KEY_PASSWORD'
    )
    $missing = @()
    foreach ($name in $required) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value)) {
            $missing += $name
        }
    }

    $keystorePath = [Environment]::GetEnvironmentVariable('PAST_PARTNER_ANDROID_KEYSTORE_FILE')
    if (![string]::IsNullOrWhiteSpace($keystorePath) -and !(Test-Path -LiteralPath $keystorePath -PathType Leaf)) {
        $missing += 'PAST_PARTNER_ANDROID_KEYSTORE_FILE'
    }

    if ($missing.Count -gt 0) {
        $uniqueMissing = $missing | Sort-Object -Unique
        throw "Android store-release signing configuration is incomplete: $($uniqueMissing -join ', ')."
    }
}

function Invoke-MobileApkBuild {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Output,
        [Parameter(Mandatory)][string]$Flutter,
        [switch]$NoBuild,
        [switch]$StoreRelease
    )

    $mobileRoot = (Resolve-Path -LiteralPath (Join-Path $Root 'mobile')).Path
    $pubspecPath = Join-Path $mobileRoot 'pubspec.yaml'
    $version = Get-MobileVersion -PubspecPath $pubspecPath
    $timestamp = Get-Date
    Remove-MobileApkOutputs -Directory $Output
    $buildKinds = if ($StoreRelease) { @('release') } else { @('debug', 'release') }
    if ($StoreRelease) {
        Assert-StoreReleaseEnvironment
    }

    if (!$NoBuild) {
        Push-Location $mobileRoot
        try {
            & $Flutter pub get
            if ($LASTEXITCODE -ne 0) { throw 'Flutter dependency resolution failed.' }
            $previousStoreRelease = [Environment]::GetEnvironmentVariable('PAST_PARTNER_ANDROID_STORE_RELEASE')
            if ($StoreRelease) {
                $env:PAST_PARTNER_ANDROID_STORE_RELEASE = 'true'
            }
            try {
                foreach ($kind in $buildKinds) {
                    & $Flutter build apk "--$kind"
                    if ($LASTEXITCODE -ne 0) { throw "$kind APK build failed." }
                }
            }
            finally {
                if ($null -eq $previousStoreRelease) {
                    Remove-Item Env:PAST_PARTNER_ANDROID_STORE_RELEASE -ErrorAction SilentlyContinue
                }
                else {
                    $env:PAST_PARTNER_ANDROID_STORE_RELEASE = $previousStoreRelease
                }
            }
        }
        finally {
            Pop-Location
        }
    }

    $sources = @{
        debug = Join-Path $mobileRoot 'build\app\outputs\flutter-apk\app-debug.apk'
        release = Join-Path $mobileRoot 'build\app\outputs\flutter-apk\app-release.apk'
    }
    $artifacts = @()
    foreach ($kind in $buildKinds) {
        $source = $sources[$kind]
        if (!(Test-Path -LiteralPath $source)) {
            throw "Missing $kind APK build artifact."
        }
        $destination = Join-Path $Output (Get-MobileApkName -Version $version -Timestamp $timestamp -Kind $kind)
        Copy-Item -LiteralPath $source -Destination $destination -Force
        $artifacts += (Get-Item -LiteralPath $destination)
    }

    # Keep the named delivery APKs only; Flutter's generated APKs can be rebuilt
    # and otherwise consume disk space across every acceptance cycle.
    foreach ($kind in $buildKinds) {
        Remove-Item -LiteralPath $sources[$kind] -Force -ErrorAction SilentlyContinue
    }
    return $artifacts
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-MobileApkBuild -Root $ProjectRoot -Output $OutputDirectory -Flutter $FlutterExecutable -NoBuild:$SkipBuild -StoreRelease:$StoreRelease |
        Select-Object FullName, Length, LastWriteTime
}
