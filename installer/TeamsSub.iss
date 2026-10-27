#ifndef AppVersion
  #define AppVersion "0.1.12"
#endif
#ifndef BinaryPath
  #define BinaryPath "..\dist\TeamsSub.exe"
#endif

[Setup]
AppId={{858D2E37-43CC-4A09-89A4-6C925937A582}
AppName=TeamsSub
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
AppPublisher=TeamsSub contributors
AppPublisherURL=https://github.com/JeremyWangCY/teamsSub
AppSupportURL=https://github.com/JeremyWangCY/teamsSub/issues
AppUpdatesURL=https://github.com/JeremyWangCY/teamsSub/releases
DefaultDirName={%USERPROFILE}\Apps\TeamsSub
DefaultGroupName=TeamsSub
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
OutputDir=..\dist
OutputBaseFilename=TeamsSub-Setup-{#AppVersion}
SetupIconFile=..\teamsub.ico
UninstallDisplayIcon={app}\TeamsSub.exe
LicenseFile=..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\TeamsSub_SingleInstance
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Explicit allowlist: never include local configuration, caches, logs or meetings.
Source: "{#BinaryPath}"; DestDir: "{app}"; DestName: "TeamsSub.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\TeamsSub"; Filename: "{app}\TeamsSub.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\TeamsSub"; Filename: "{app}\TeamsSub.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\TeamsSub.exe"; Description: "{cm:LaunchProgram,TeamsSub}"; Flags: nowait postinstall skipifsilent

; No UninstallDelete entries: user configuration and meeting records survive uninstall.
