if ($global:HansExperimentControlsLoaded) {
    Write-Host 'Experiment controls are already loaded in this terminal.'
    return
}

$global:HansExperimentPreviousRAlias = Get-Alias -Name r -ErrorAction SilentlyContinue

$adbCommand = Get-Command adb -ErrorAction SilentlyContinue
if ($adbCommand) {
    $global:HansExperimentAdbPath = $adbCommand.Source
} else {
    $sdkRoots = @(
        $env:ANDROID_SDK_ROOT,
        $env:ANDROID_HOME,
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
    ) | Where-Object { $_ }

    $adbPath = $sdkRoots |
        ForEach-Object { Join-Path $_ 'platform-tools\adb.exe' } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1

    $global:HansExperimentAdbPath = if ($adbPath) { $adbPath } else { 'adb' }
}

$global:HansExperimentPackage = if ($env:HANS_PACKAGE) {
    $env:HANS_PACKAGE
} else {
    'com.example.hans.arcore'
}

if ($global:HansExperimentPreviousRAlias) {
    Remove-Item Alias:\r -Force
}

function global:Send-HansExperimentCommand {
    param([Parameter(Mandatory = $true)][string]$Command)

    & $global:HansExperimentAdbPath shell am broadcast `
        -a com.example.hans.EXPERIMENT_COMMAND `
        -p $global:HansExperimentPackage `
        --es command $Command

    if ($LASTEXITCODE -ne 0) {
        throw "ADB failed to send experiment command '$Command' (exit code $LASTEXITCODE)."
    }
}

function global:s { Send-HansExperimentCommand 's' }
function global:r { Send-HansExperimentCommand 'r' }
function global:t { Send-HansExperimentCommand 't' }
function global:o { Send-HansExperimentCommand 'o' }
function global:y { Send-HansExperimentCommand 'y' }
function global:n { Send-HansExperimentCommand 'n' }
function global:f { Send-HansExperimentCommand 'f' }
function global:c { Send-HansExperimentCommand 'c' }
$global:HansExperimentControlsLoaded = $true

function global:Remove-HansExperimentControls {
    foreach ($name in @(
        'Send-HansExperimentCommand', 's', 'r', 't', 'o', 'y', 'n', 'f', 'c',
        'Remove-HansExperimentControls'
    )) {
        Remove-Item "Function:\$name" -ErrorAction SilentlyContinue
    }

    if ($global:HansExperimentPreviousRAlias) {
        Set-Alias -Name r -Value $global:HansExperimentPreviousRAlias.Definition -Scope Global -Force
    }

    Remove-Variable HansExperimentAdbPath, HansExperimentPackage, HansExperimentPreviousRAlias, `
        HansExperimentControlsLoaded `
        -Scope Global -ErrorAction SilentlyContinue
}

Write-Host "Experiment controls loaded for $global:HansExperimentPackage."
Write-Host 'Type s, r, t, o, y, n, f, or c to send a command.'
Write-Host 'Run Remove-HansExperimentControls to restore the original PowerShell r alias.'
