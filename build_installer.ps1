param(
    [string]$IsccPath,
    [string]$BinaryPath = (Join-Path $PSScriptRoot 'dist\TeamsSub.exe'),
    [string]$OutputDir = (Join-Path $PSScriptRoot 'dist')
)
$ErrorActionPreference = 'Stop'
if (-not $IsccPath) {
    $candidatePaths = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe')
    )
    $IsccPath = $candidatePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw 'Inno Setup compiler not found. Install Inno Setup or pass -IsccPath.'
}
if (-not (Test-Path -LiteralPath $BinaryPath)) {
    throw 'TeamsSub.exe not found. Run build_exe.bat first or pass -BinaryPath.'
}
$versionMatch = [regex]::Match([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'teamsub.py')), 'APP_VERSION = "(\d+\.\d+\.\d+)"')
if (-not $versionMatch.Success) { throw 'APP_VERSION could not be read.' }
$version = $versionMatch.Groups[1].Value
$binaryVersion = (Get-Item -LiteralPath $BinaryPath).VersionInfo.FileVersion
if ($binaryVersion -ne $version) { throw "Binary version $binaryVersion differs from source version $version." }
$resolvedBinary = (Resolve-Path -LiteralPath $BinaryPath).Path
$resolvedOutput = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
& $IsccPath "/DAppVersion=$version" "/DBinaryPath=$resolvedBinary" "/O$resolvedOutput" (Join-Path $PSScriptRoot 'installer\TeamsSub.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
Get-FileHash -LiteralPath (Join-Path $resolvedOutput "TeamsSub-Setup-$version.exe") -Algorithm SHA256
