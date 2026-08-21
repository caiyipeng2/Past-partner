[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path $PSScriptRoot 'build_mobile_apk.ps1'
. $scriptPath

$temp = Join-Path ([IO.Path]::GetTempPath()) ('past-partner-apk-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $pubspec = Join-Path $temp 'pubspec.yaml'
    Set-Content -LiteralPath $pubspec -Value "name: past_partner`nversion: 0.1.0+1`n"
    $version = Get-MobileVersion -PubspecPath $pubspec
    if ($version -ne '0.1.0+1') { throw 'Version parsing assertion failed.' }

    $timestamp = [datetime]::ParseExact('20260813_1930', 'yyyyMMdd_HHmm', $null)
    $name = Get-MobileApkName -Version $version -Timestamp $timestamp -Kind release
    if ($name -ne 'Past-partner_0.1.0+1_20260813_1930_release.apk') {
        throw "APK naming assertion failed: $name"
    }

    $keystore = Join-Path $temp 'release.keystore'
    Set-Content -LiteralPath $keystore -Value 'disposable'
    $signingNames = @(
        'PAST_PARTNER_ANDROID_KEYSTORE_FILE',
        'PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD',
        'PAST_PARTNER_ANDROID_KEY_ALIAS',
        'PAST_PARTNER_ANDROID_KEY_PASSWORD'
    )
    $previousSigning = @{}
    foreach ($name in $signingNames) {
        $previousSigning[$name] = [Environment]::GetEnvironmentVariable($name)
    }
    try {
        $env:PAST_PARTNER_ANDROID_KEYSTORE_FILE = $keystore
        $env:PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD = 'store-secret'
        $env:PAST_PARTNER_ANDROID_KEY_ALIAS = 'past-partner'
        $env:PAST_PARTNER_ANDROID_KEY_PASSWORD = 'key-secret'
        Assert-StoreReleaseEnvironment

        Remove-Item Env:PAST_PARTNER_ANDROID_KEY_ALIAS
        $rejected = $false
        try {
            Assert-StoreReleaseEnvironment
        }
        catch {
            $rejected = $true
            if ($_.Exception.Message -match 'key-secret|store-secret|release\.keystore') {
                throw 'Signing validation leaked a secret value or path.'
            }
        }
        if (!$rejected) { throw 'Missing signing value was accepted.' }
    }
    finally {
        foreach ($name in $signingNames) {
            if ($null -eq $previousSigning[$name]) {
                Remove-Item "Env:$name" -ErrorAction SilentlyContinue
            }
            else {
                Set-Item "Env:$name" $previousSigning[$name]
            }
        }
    }

    $output = Join-Path $temp 'output'
    New-Item -ItemType Directory -Path $output | Out-Null
    Set-Content -LiteralPath (Join-Path $output 'Past-partner_0.0.1_20200101_0000_debug.apk') -Value 'old'
    Set-Content -LiteralPath (Join-Path $output 'keep.txt') -Value 'keep'
    Remove-MobileApkOutputs -Directory $output
    if (Test-Path -LiteralPath (Join-Path $output 'Past-partner_0.0.1_20200101_0000_debug.apk')) {
        throw 'Old APK cleanup assertion failed.'
    }
    if (!(Test-Path -LiteralPath (Join-Path $output 'keep.txt'))) {
        throw 'Non-APK file was removed.'
    }
    Write-Output 'build_mobile_apk_test: PASS'
}
finally {
    if (Test-Path -LiteralPath $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
}
