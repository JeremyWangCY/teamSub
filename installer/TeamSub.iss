#ifndef AppVersion
  #define AppVersion "0.2.1"
#endif
#ifndef BinaryPath
  #define BinaryPath "..\dist\TeamSub.exe"
#endif
#ifndef UpdateWaitTimeout
  #define UpdateWaitTimeout 90000
#endif

[Setup]
AppId={{858D2E37-43CC-4A09-89A4-6C925937A582}
AppName=TeamSub
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
AppPublisher=TeamSub contributors
AppPublisherURL=https://github.com/JeremyWangCY/teamSub
AppSupportURL=https://github.com/JeremyWangCY/teamSub/issues
AppUpdatesURL=https://github.com/JeremyWangCY/teamSub/releases
DefaultDirName={%USERPROFILE}\Apps\TeamSub
DefaultGroupName=TeamSub
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
OutputDir=..\dist
OutputBaseFilename=TeamSub-Setup-{#AppVersion}
SetupIconFile=..\teamsub.ico
UninstallDisplayIcon={app}\TeamSub.exe
LicenseFile=..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\TeamSub_SingleInstance
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Explicit allowlist: never include local configuration, caches, logs or meetings.
Source: "{#BinaryPath}"; DestDir: "{app}"; DestName: "TeamSub.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\TeamSub"; Filename: "{app}\TeamSub.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\TeamSub"; Filename: "{app}\TeamSub.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\TeamSub.exe"; Description: "{cm:LaunchProgram,TeamSub}"; Flags: nowait postinstall skipifsilent; Check: not IsAutomaticUpdate

; No UninstallDelete entries: user configuration and meeting records survive uninstall.

[Code]
const
  UpdateAppMutex = 'Local\TeamSub_SingleInstance';
  SynchronizeAccess = $00100000;
  WaitObject = 0;
  WaitTimeout = $00000102;
  InvalidProcess = 87;
  NoSuchFile = 2;

var
  AutomaticUpdate: Boolean;
  UpdateRecoveryAllowed: Boolean;
  UpdateInstalled: Boolean;
  UpdateRecoveryAttempted: Boolean;
  UpdateDirectory: String;
  UpdateBackup: String;
  UpdateBackupHash: String;

function OpenProcess(Access: LongWord; InheritHandle: Boolean; ProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function OpenMutex(Access: LongWord; InheritHandle: Boolean; Name: String): THandle;
  external 'OpenMutexW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function Win32LastError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';

function IsAutomaticUpdate: Boolean;
begin
  Result := AutomaticUpdate;
end;

function AppInstanceRunning: Boolean;
var
  Handle: THandle;
  ErrorCode: LongWord;
begin
  Handle := OpenMutex(SynchronizeAccess, False, UpdateAppMutex);
  ErrorCode := Win32LastError;
  if Handle <> 0 then begin
    CloseHandle(Handle);
    Result := True;
  end else
    { Access errors are not evidence that it is safe to replace a running app. }
    Result := ErrorCode <> NoSuchFile;
end;

function ValidBackup: Boolean;
begin
  Result := False;
  if not FileExists(UpdateBackup) then
    Exit;
  try
    Result := (Length(UpdateBackupHash) = 64) and
      (CompareText(GetSHA256OfFile(UpdateBackup), UpdateBackupHash) = 0);
  except
    Log('Cannot verify the previous application backup.');
  end;
end;

function WaitForProcessExit(ProcessId: Integer; var Elapsed: Integer): Boolean;
var
  Handle: THandle;
  ErrorCode, Status: LongWord;
begin
  Result := ProcessId = 0;
  if Result then
    Exit;
  Handle := OpenProcess(SynchronizeAccess, False, ProcessId);
  ErrorCode := Win32LastError;
  if Handle = 0 then begin
    Result := ErrorCode = InvalidProcess;
    if not Result then
      Log(Format('Cannot wait for application process %d (error %d).', [ProcessId, ErrorCode]));
    Exit;
  end;
  try
    while Elapsed < {#UpdateWaitTimeout} do begin
      Status := WaitForSingleObject(Handle, 100);
      if Status = WaitObject then begin
        Result := True;
        Exit;
      end;
      if Status <> WaitTimeout then begin
        Log('Waiting for the previous application failed.');
        Exit;
      end;
      Elapsed := Elapsed + 100;
    end;
    Log('Previous application did not exit before the update timeout; leaving it unchanged.');
  finally
    CloseHandle(Handle);
  end;
end;

function InitializeSetup: Boolean;
var
  ProcessId, ParentProcessId, Elapsed: Integer;
  Target: String;
begin
  Result := True;
  AutomaticUpdate := ExpandConstant('{param:TeamSubUpdate|0}') = '1';
  if not AutomaticUpdate then
    Exit;
  Result := False;
  ProcessId := StrToIntDef(ExpandConstant('{param:TeamSubWaitPid|0}'), 0);
  ParentProcessId := StrToIntDef(ExpandConstant('{param:TeamSubWaitParentPid|0}'), 0);
  UpdateDirectory := RemoveBackslashUnlessRoot(ExpandConstant('{param:DIR|}'));
  UpdateBackup := ExpandConstant('{param:TeamSubBackup|}');
  UpdateBackupHash := ExpandConstant('{param:TeamSubBackupSHA256|}');
  { The app constant is unavailable before wizard initialization; use the explicit DIR. }
  if (ProcessId <= 0) or (ParentProcessId < 0) or (Length(UpdateDirectory) < 3) or
     not ((Copy(UpdateDirectory, 2, 2) = ':\') or (Copy(UpdateDirectory, 1, 2) = '\\')) or
     (CompareText(ExtractFileName(UpdateBackup), 'TeamSub.previous.exe') <> 0) or
     (CompareText(ExtractFileDir(UpdateBackup), UpdateDirectory) = 0) or not ValidBackup then begin
    Log('Invalid automatic update parameters or backup; installation cancelled.');
    Exit;
  end;
  Target := AddBackslash(UpdateDirectory) + 'TeamSub.exe';
  if not FileExists(Target) then begin
    Log('Automatic update target does not exist; installation cancelled.');
    Exit;
  end;
  try
    if CompareText(GetSHA256OfFile(Target), UpdateBackupHash) <> 0 then begin
      Log('Automatic update target does not match the saved previous application.');
      Exit;
    end;
  except
    Log('Cannot verify the current automatic update target.');
    Exit;
  end;
  Elapsed := 0;
  if not WaitForProcessExit(ProcessId, Elapsed) or
     not WaitForProcessExit(ParentProcessId, Elapsed) then
    Exit;
  if AppInstanceRunning or not ValidBackup then begin
    Log('An application instance is still running or the backup changed; update cancelled.');
    Exit;
  end;
  UpdateRecoveryAllowed := True;
  Result := True;
end;

procedure RecoverPreviousApplication;
var
  ExitCode: Integer;
  Target: String;
begin
  if not AutomaticUpdate or not UpdateRecoveryAllowed or UpdateRecoveryAttempted or
     UpdateInstalled or AppInstanceRunning then
    Exit;
  UpdateRecoveryAttempted := True;
  if not ValidBackup then begin
    Log('Cannot recover: previous application backup verification failed.');
    Exit;
  end;
  Target := AddBackslash(UpdateDirectory) + 'TeamSub.exe';
  if not CopyFile(UpdateBackup, Target, False) then begin
    Log('Cannot recover the previous application file. The verified backup is retained.');
    Exit;
  end;
  if Exec(Target, '', UpdateDirectory, SW_SHOWNORMAL, ewNoWait, ExitCode) then
    Log('Previous application restored and restarted after the failed update.')
  else
    Log(Format('Previous application restored; restart failed (error %d).', [ExitCode]));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
begin
  if CurStep = ssPostInstall then
    UpdateInstalled := True;
  if (CurStep = ssDone) and AutomaticUpdate and UpdateInstalled then begin
    if Exec(ExpandConstant('{app}\TeamSub.exe'), '', ExpandConstant('{app}'),
            SW_SHOWNORMAL, ewNoWait, ExitCode) then
      Log('Updated application started automatically.')
    else begin
      Log(Format('Updated application could not start (error %d); recovering previous version.', [ExitCode]));
      UpdateInstalled := False;
      RecoverPreviousApplication;
    end;
  end;
end;

procedure DeinitializeSetup;
begin
  RecoverPreviousApplication;
end;
