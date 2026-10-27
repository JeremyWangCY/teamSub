param(
    [Parameter(Mandatory = $true)][string]$IsccPath,
    [Parameter(Mandatory = $true)][string]$OutputRoot
)

# Windows integration tests use an isolated application identity and a tiny .NET
# payload. They never launch, terminate or modify the user's TeamsSub process.
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path $PSScriptRoot -Parent
$runId = [Guid]::NewGuid().ToString('N')
$sandbox = Join-Path ([IO.Path]::GetFullPath($OutputRoot)) ('installer-update-' + $runId)
$mutexName = 'Local\TeamsSub_Update_Test_' + $runId
$appId = [Guid]::NewGuid().ToString().ToUpperInvariant()
$utf8 = New-Object Text.UTF8Encoding($true)
New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
$holds = @()
$results = [Collections.Generic.List[object]]::new()

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function File-Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
function Wait-ForFile([string]$Path) {
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $Path)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "Timed out waiting for $Path" }
        Start-Sleep -Milliseconds 50
    }
}
function Wait-Exit($Process) {
    Assert-True ($Process.WaitForExit(30000)) 'Isolated installer timed out'
    $Process.Refresh()
    return $Process.ExitCode
}
function Start-Hold([string]$Exe, [string]$Suffix) {
    $gate = Join-Path (Split-Path $Exe) ($Suffix + '.gate')
    $ready = Join-Path (Split-Path $Exe) ($Suffix + '.ready')
    $process = Start-Process -FilePath $Exe -ArgumentList @('--hold', ('"' + $gate + '"'), ('"' + $mutexName + '"'), ('"' + $ready + '"')) -WindowStyle Hidden -PassThru
    $hold = @{ Process = $process; Gate = $gate }
    $script:holds += $hold
    Wait-ForFile $ready
    return $hold
}
function Release-Hold($Hold) {
    [IO.File]::WriteAllText($Hold.Gate, 'exit')
    Assert-True ($Hold.Process.WaitForExit(10000)) 'Isolated host did not exit'
}
function New-Case([string]$Name) {
    $folder = Join-Path $sandbox $Name
    $app = Join-Path $folder 'app Chinese 空格'
    $download = Join-Path $folder 'download'
    New-Item -ItemType Directory -Path (Join-Path $app 'captions'), $download -Force | Out-Null
    $exe = Join-Path $app 'TeamsSub.exe'
    $backup = Join-Path $download 'TeamsSub.previous.exe'
    Copy-Item -LiteralPath $oldPayload -Destination $exe
    Copy-Item -LiteralPath $oldPayload -Destination $backup
    $config = Join-Path $app 'appconfig.json'
    $meeting = Join-Path $app 'captions\fixture.jsonl'
    [IO.File]::WriteAllText($config, '{"isolated_fixture":true}')
    [IO.File]::WriteAllText($meeting, '{"original":"preserve meeting","translation":"保留记录"}')
    return @{ Folder = $folder; App = $app; Exe = $exe; Backup = $backup; Hash = (File-Hash $backup); Config = $config; Meeting = $meeting; ConfigHash = (File-Hash $config); MeetingHash = (File-Hash $meeting); Marker = (Join-Path $app 'restart.marker') }
}
function Assert-Data($Case) {
    Assert-True ((File-Hash $Case.Config) -eq $Case.ConfigHash) 'Configuration fixture changed'
    Assert-True ((File-Hash $Case.Meeting) -eq $Case.MeetingHash) 'Meeting fixture changed'
}
function Start-Update($Case, $Hold, [int]$ParentId = 0, [string]$Setup = $normalSetup, [string]$BackupHash = $Case.Hash) {
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS', '/TeamsSubUpdate=1',
        ('/TeamsSubWaitPid=' + $Hold.Process.Id), ('/TeamsSubWaitParentPid=' + $ParentId),
        ('/DIR="' + $Case.App + '"'), ('/TeamsSubBackup="' + $Case.Backup + '"'),
        ('/TeamsSubBackupSHA256=' + $BackupHash), ('/LOG="' + (Join-Path $Case.Folder 'install.log') + '"'))
    return Start-Process -FilePath $Setup -ArgumentList $arguments -WindowStyle Hidden -PassThru
}
function Record([string]$Name) { $results.Add(@{ Name = $Name; Passed = $true }) }

$probeSource = @'
using System;
using System.IO;
using System.Threading;
using System.Reflection;
[assembly: AssemblyVersion("1.0.0.0")]
class Probe {
    static void Main(string[] args) {
        if (args.Length == 4 && args[0] == "--hold") {
            using (var mutex = new Mutex(false, args[2])) {
                File.WriteAllText(args[3], "ready");
                while (!File.Exists(args[1])) Thread.Sleep(20);
                GC.KeepAlive(mutex);
            }
            return;
        }
#if OLD
        const string version = "old";
#else
        const string version = "new";
#endif
        File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "restart.marker"), version);
    }
}
'@
$probeFile = Join-Path $sandbox 'Probe.cs'
[IO.File]::WriteAllText($probeFile, $probeSource, $utf8)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$oldPayload = Join-Path $sandbox 'old.exe'
$newPayload = Join-Path $sandbox 'new.exe'
& $compiler /nologo /target:winexe /define:OLD ("/out:$oldPayload") $probeFile
Assert-True ($LASTEXITCODE -eq 0) 'Old probe compilation failed'
& $compiler /nologo /target:winexe ("/out:$newPayload") $probeFile
Assert-True ($LASTEXITCODE -eq 0) 'New probe compilation failed'
$source = [IO.File]::ReadAllText((Join-Path $sourceRoot 'installer\TeamsSub.iss'))
$source = $source.Replace('858D2E37-43CC-4A09-89A4-6C925937A582', $appId)
$source = $source.Replace('TeamsSub_SingleInstance', ('TeamsSub_Update_Test_' + $runId))
$source = $source.Replace('AppName=TeamsSub', 'AppName=TeamsSub Isolated Update Test')
$source = $source.Replace('DefaultGroupName=TeamsSub', ('DefaultGroupName=TeamsSub Isolated ' + $runId))
$source = $source.Replace('UsePreviousAppDir=yes', 'UsePreviousAppDir=no')
$source = [Text.RegularExpressions.Regex]::Replace($source, '(?ms)^\[Icons\].*?(?=^\[Run\])', "[Icons]`r`n`r`n")
$source = $source.Replace('..\teamsub.ico', (Join-Path $sourceRoot 'teamsub.ico'))
$source = $source.Replace('..\LICENSE', (Join-Path $sourceRoot 'LICENSE'))
$source = $source.Replace('OutputBaseFilename=TeamsSub-Setup-{#AppVersion}', 'OutputBaseFilename=Isolated-Update')
$normalSource = Join-Path $sandbox 'Normal.iss'
[IO.File]::WriteAllText($normalSource, $source, $utf8)
& $IsccPath ("/DBinaryPath=$newPayload") '/DUpdateWaitTimeout=4000' ("/O$sandbox") $normalSource *> (Join-Path $sandbox 'compile.log')
Assert-True ($LASTEXITCODE -eq 0) 'Isolated installer compilation failed'
$normalSetup = Join-Path $sandbox 'Isolated-Update.exe'
$invalidPayload = Join-Path $sandbox 'invalid.exe'
$invalidBytes = New-Object byte[] 512
$invalidBytes[0] = 77
$invalidBytes[1] = 90
[IO.File]::WriteAllBytes($invalidPayload, $invalidBytes)
$launchFailureSource = Join-Path $sandbox 'LaunchFailure.iss'
[IO.File]::WriteAllText($launchFailureSource, $source.Replace('OutputBaseFilename=Isolated-Update', 'OutputBaseFilename=Isolated-Launch-Failure'), $utf8)
& $IsccPath ("/DBinaryPath=$invalidPayload") '/DUpdateWaitTimeout=4000' ("/O$sandbox") $launchFailureSource *> (Join-Path $sandbox 'compile-launch-failure.log')
Assert-True ($LASTEXITCODE -eq 0) 'Launch failure installer compilation failed'
$launchFailureSetup = Join-Path $sandbox 'Isolated-Launch-Failure.exe'

try {
    $case = New-Case 'success'
    $hostProcess = Start-Hold $case.Exe 'host'
    $parentProcess = Start-Hold $case.Exe 'parent'
    $install = Start-Update $case $hostProcess $parentProcess.Process.Id
    Start-Sleep -Milliseconds 700
    Assert-True (-not $install.HasExited) 'Installer did not wait for the application'
    Assert-True ((File-Hash $case.Exe) -eq $case.Hash) 'Installer replaced the running application'
    Release-Hold $hostProcess
    Start-Sleep -Milliseconds 500
    Assert-True (-not $install.HasExited) 'Installer did not wait for the parent process'
    Assert-True ((File-Hash $case.Exe) -eq $case.Hash) 'Installer replaced the running parent application'
    Release-Hold $parentProcess
    Assert-True ((Wait-Exit $install) -eq 0) 'Successful automatic update failed'
    Wait-ForFile $case.Marker
    Assert-True ([IO.File]::ReadAllText($case.Marker) -eq 'new') 'New application was not automatically started'
    Assert-True ((File-Hash $case.Exe) -eq (File-Hash $newPayload)) 'Installed application differs from payload'
    Assert-Data $case
    Record 'waits_for_both_processes_then_installs_and_restarts_preserving_data'

    $case = New-Case 'timeout'
    $hostProcess = Start-Hold $case.Exe 'host'
    $install = Start-Update $case $hostProcess
    Assert-True ((Wait-Exit $install) -ne 0) 'Waiting timeout unexpectedly succeeded'
    Assert-True (-not $hostProcess.Process.HasExited) 'Waiting timeout terminated the old application'
    Assert-True ((File-Hash $case.Exe) -eq $case.Hash) 'Waiting timeout modified the old application'
    Assert-True (-not (Test-Path -LiteralPath $case.Marker)) 'Waiting timeout restarted another application'
    Assert-Data $case
    Release-Hold $hostProcess
    Record 'wait_timeout_keeps_running_old_process_and_files'

    $case = New-Case 'invalid-hash'
    $hostProcess = Start-Hold $case.Exe 'host'
    $install = Start-Update $case $hostProcess 0 $normalSetup ('0' * 64)
    Assert-True ((Wait-Exit $install) -ne 0) 'Invalid backup hash unexpectedly succeeded'
    Assert-True (-not $hostProcess.Process.HasExited) 'Invalid backup terminated the old application'
    Assert-True ((File-Hash $case.Exe) -eq $case.Hash) 'Invalid backup modified the application'
    Assert-True (-not (Test-Path -LiteralPath $case.Marker)) 'Invalid backup restarted an application'
    Assert-Data $case
    Release-Hold $hostProcess
    Record 'invalid_backup_hash_is_rejected_without_changes'

    $case = New-Case 'failure'
    $hostProcess = Start-Hold $case.Exe 'host'
    # The executable is copied before LICENSE.txt. An exclusive lock produces a
    # real installation I/O failure after replacement, rather than a script
    # event exception that Inno may merely log and suppress.
    $lockedFile = Join-Path $case.App 'LICENSE.txt'
    [IO.File]::WriteAllText($lockedFile, 'locked isolated fixture')
    $lock = [IO.File]::Open($lockedFile, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $install = Start-Update $case $hostProcess
        Start-Sleep -Milliseconds 400
        Release-Hold $hostProcess
        Assert-True ((Wait-Exit $install) -ne 0) 'Injected install failure unexpectedly succeeded'
    }
    finally { $lock.Dispose() }
    Wait-ForFile $case.Marker
    Assert-True ([IO.File]::ReadAllText($case.Marker) -eq 'old') 'Previous application was not restarted after failure'
    Assert-True ((File-Hash $case.Exe) -eq $case.Hash) 'Previous application was not restored after failure'
    Assert-Data $case
    Record 'failure_after_file_replacement_restores_and_restarts_verified_previous_app'

    $case = New-Case 'launch-failure'
    $hostProcess = Start-Hold $case.Exe 'host'
    $install = Start-Update $case $hostProcess 0 $launchFailureSetup
    Start-Sleep -Milliseconds 400
    Release-Hold $hostProcess
    $null = Wait-Exit $install
    Wait-ForFile $case.Marker
    Assert-True ([IO.File]::ReadAllText($case.Marker) -eq 'old') 'Launch failure did not restart the old application'
    Assert-True ((File-Hash $case.Exe) -eq $case.Hash) 'Launch failure did not restore the old application'
    Assert-Data $case
    Record 'new_executable_launch_failure_restores_and_restarts_previous_app'
}
finally {
    foreach ($hold in $holds) {
        if (-not $hold.Process.HasExited) { Release-Hold $hold }
    }
    foreach ($uninstaller in (Get-ChildItem -LiteralPath $sandbox -Recurse -Filter 'unins*.exe')) {
        $uninstall = Start-Process -FilePath $uninstaller.FullName -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -WindowStyle Hidden -PassThru
        Assert-True ((Wait-Exit $uninstall) -eq 0) 'Isolated uninstall failed'
    }
    $report = [ordered]@{
        Passed = ($results.Count -eq 5)
        Payload = 'Isolated .NET WinExe probe, not the production application'
        Installer = 'Production installer source with separate AppId/mutex, no shortcuts; real I/O failure from an exclusively locked fixture'
        Platform = [Environment]::OSVersion.VersionString
        Cases = @($results)
        Sandbox = $sandbox
    }
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $sandbox 'report.json') -Encoding UTF8
    $report | ConvertTo-Json -Depth 5
}
