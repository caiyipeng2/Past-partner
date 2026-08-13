[CmdletBinding()]
param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..'),
    [string]$OutputDirectory = 'E:\Tools',
    [string]$FlutterExecutable = 'flutter',
    [switch]$SkipBuild
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

function Invoke-MobileApkBuild {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Output,
        [Parameter(Mandatory)][string]$Flutter,
        [switch]$NoBuild
    )

    $mobileRoot = (Resolve-Path -LiteralPath (Join-Path $Root 'mobile')).Path
    $pubspecPath = Join-Path $mobileRoot 'pubspec.yaml'
    $version = Get-MobileVersion -PubspecPath $pubspecPath
    $timestamp = Get-Date
    Remove-MobileApkOutputs -Directory $Output

    if (!$NoBuild) {
        Push-Location $mobileRoot
        try {
            & $Flutter pub get
            if ($LASTEXITCODE -ne 0) { throw 'Flutter dependency resolution failed.' }
            & $Flutter build apk --debug
            if ($LASTEXITCODE -ne 0) { throw 'Debug APK build failed.' }
            & $Flutter build apk --release
            if ($LASTEXITCODE -ne 0) { throw 'Release APK build failed.' }
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
    foreach ($kind in @('debug', 'release')) {
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
    foreach ($kind in @('debug', 'release')) {
        Remove-Item -LiteralPath $sources[$kind] -Force -ErrorAction SilentlyContinue
    }
    return $artifacts
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-MobileApkBuild -Root $ProjectRoot -Output $OutputDirectory -Flutter $FlutterExecutable -NoBuild:$SkipBuild |
        Select-Object FullName, Length, LastWriteTime
}
