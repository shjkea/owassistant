[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$NoVenvRefresh
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function New-BuildVenv {
    param([Parameter(Mandatory=$true)][string]$Destination)

    if ($PythonExe) {
        if (-not (Test-Path $PythonExe)) {
            throw "Python executable not found: $PythonExe"
        }
        Invoke-Checked -FilePath $PythonExe -Arguments @('-m', 'venv', $Destination)
        return
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3.14 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,14) else 1)'
        if ($LASTEXITCODE -eq 0) {
            & $pyLauncher.Source -3.14 -m venv $Destination
            if ($LASTEXITCODE -ne 0) {
                throw 'Failed to create build venv with py -3.14.'
            }
            return
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw 'Python 3.14 was not found. Use -PythonExe with the full path to python.exe.'
    }

    & $python.Source -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3,14) else 1)'
    if ($LASTEXITCODE -ne 0) {
        throw 'The current python command is not Python 3.14. Use -PythonExe with the full path to Python 3.14.'
    }

    Invoke-Checked -FilePath $python.Source -Arguments @('-m', 'venv', $Destination)
}

$VenvDir = Join-Path $Root '.venv-build'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    Write-Host '==> Creating isolated build environment: .venv-build' -ForegroundColor Cyan
    New-BuildVenv -Destination $VenvDir
}

if (-not $NoVenvRefresh) {
    Write-Host '==> Installing/updating runtime and packaging dependencies' -ForegroundColor Cyan
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '-r', 'requirements.txt')
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '-r', 'packaging\windows\requirements-build.txt')
}

$VersionOutput = & $VenvPython 'packaging\windows\generate_version_info.py'
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to generate Windows version information.'
}

$Version = ($VersionOutput | Select-Object -Last 1).Trim()
if (-not $Version) {
    throw 'Could not read the application version.'
}
Write-Host "==> Building version v$Version" -ForegroundColor Cyan

if (-not $SkipTests) {
    Write-Host '==> Running pre-release tests' -ForegroundColor Cyan
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pytest', '-q', 'owstats\tests')
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pytest', '-q', 'ai_review_backend\tests')

    $OldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = (Join-Path $Root 'bnet_switcher_backend')
        Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pytest', '-q', 'bnet_switcher_backend\tests')
    }
    finally {
        $env:PYTHONPATH = $OldPythonPath
    }

    # Qt GUI test modules own application/window state. Run every module in
    # a fresh process so teardown state cannot leak into the next module.
    # No tests are skipped; any assertion failure or native crash still stops the build.
    $OldQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = 'offscreen'
        $UiTestFiles = @(Get-ChildItem (Join-Path $Root 'tests') -Recurse -File -Filter 'test_*.py' | Sort-Object FullName)
        if ($UiTestFiles.Count -eq 0) {
            throw 'No UI test modules were found.'
        }
        foreach ($TestFile in $UiTestFiles) {
            Write-Host "==> Testing $($TestFile.Name) in an isolated process" -ForegroundColor DarkGray
            Invoke-Checked -FilePath $VenvPython -Arguments @('-X', 'faulthandler', '-m', 'pytest', '-q', $TestFile.FullName)
        }
    }
    finally {
        $env:QT_QPA_PLATFORM = $OldQtPlatform
    }
}

foreach ($dir in @('.build', 'dist', 'release')) {
    $path = Join-Path $Root $dir
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
    }
}
New-Item -ItemType Directory -Path (Join-Path $Root 'release') -Force | Out-Null

Write-Host '==> Building one-file executable for the installer' -ForegroundColor Cyan
Invoke-Checked -FilePath $VenvPython -Arguments @(
    '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--workpath', '.build\onefile', '--distpath', 'dist\onefile',
    'packaging\windows\OWAssistant_onefile.spec'
)

$OneFileExe = Join-Path $Root 'dist\onefile\OWAssistant.exe'
if (-not (Test-Path $OneFileExe)) {
    throw 'The one-file executable was not generated.'
}

Write-Host '==> Building portable distribution' -ForegroundColor Cyan
Invoke-Checked -FilePath $VenvPython -Arguments @(
    '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--workpath', '.build\portable', '--distpath', 'dist\portable',
    'packaging\windows\OWAssistant_portable.spec'
)

$PortableSource = Join-Path $Root 'dist\portable\OWAssistant'
$PortableExe = Join-Path $PortableSource 'OWAssistant.exe'
if (-not (Test-Path $PortableExe)) {
    throw 'The portable executable was not generated.'
}

# Source-only/development files must never be copied into the portable release.
foreach ($relative in @(
    '_internal\screenshoot',
    '_internal\tests',
    '_internal\tools',
    '_internal\scripts',
    '_internal\ui_reference',
    '_internal\ui_preview.py',
    '_internal\owstats_smoke_test.py',
    '_internal\owstats_ui_smoke_test.py',
    '_internal\ui_smoke_test.py'
)) {
    $candidate = Join-Path $PortableSource $relative
    if (Test-Path $candidate) {
        throw "Development file was included in the release: $relative"
    }
}

$StageRoot = Join-Path $Root '.build\portable_stage'
$PortableName = "OWAssistant_Portable_v$Version"
$PortableStage = Join-Path $StageRoot $PortableName

if (Test-Path $StageRoot) {
    Remove-Item $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
Copy-Item $PortableSource $PortableStage -Recurse -Force

$PortableZip = Join-Path $Root "release\$PortableName.zip"
Compress-Archive -Path $PortableStage -DestinationPath $PortableZip -CompressionLevel Optimal -Force

function Find-InnoSetupCompiler {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }

    $bases = @(
        $env:ProgramFiles,
        [Environment]::GetEnvironmentVariable('ProgramFiles(x86)'),
        (Join-Path $env:LOCALAPPDATA 'Programs')
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($base in $bases) {
        foreach ($version in @('7', '6')) {
            $candidate = Join-Path $base ("Inno Setup $version\ISCC.exe")
            if (Test-Path $candidate) {
                return $candidate
            }
        }
    }

    $registryRoots = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )

    foreach ($rootKey in $registryRoots) {
        $apps = Get-ItemProperty $rootKey -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like 'Inno Setup*' }
        foreach ($app in $apps) {
            if ($app.InstallLocation) {
                $candidate = Join-Path $app.InstallLocation 'ISCC.exe'
                if (Test-Path $candidate) {
                    return $candidate
                }
            }
        }
    }

    foreach ($base in $bases) {
        $found = Get-ChildItem -Path $base -Filter ISCC.exe -File -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }

    return $null
}

if (-not $SkipInstaller) {
    Write-Host '==> Building installer' -ForegroundColor Cyan

    $iscc = Find-InnoSetupCompiler
    if (-not $iscc) {
        throw 'Inno Setup compiler ISCC.exe was not found. Reinstall Inno Setup or pass its install path manually.'
    }

    Write-Host "==> Using Inno Setup compiler: $iscc" -ForegroundColor DarkGray
    Invoke-Checked -FilePath $iscc -Arguments @("/DMyAppVersion=$Version", 'packaging\windows\installer.iss')
}

$HashTargets = Get-ChildItem (Join-Path $Root 'release') -File | Where-Object {
    $_.Extension -in @('.zip', '.exe')
} | Sort-Object Name

$HashLines = foreach ($file in $HashTargets) {
    $hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Name)"
}
$HashLines | Set-Content (Join-Path $Root 'release\SHA256SUMS.txt') -Encoding ascii

Write-Host ''
Write-Host 'Build completed.' -ForegroundColor Green
Get-ChildItem (Join-Path $Root 'release') -File |
    Select-Object Name, Length, LastWriteTime |
    Format-Table -AutoSize
