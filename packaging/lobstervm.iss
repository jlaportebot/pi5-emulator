; Inno Setup script for the LobsterVM per-user installer.
; Built by packaging/make_windows_installer.py, which passes:
;   /DAppVersion=...  /DSourceDir=<dist>\LobsterVM  /DOutputDir=...  /DIconFile=...
#ifndef AppVersion
  #error AppVersion is not defined
#endif

[Setup]
AppId={{6F1D3B52-9C0A-4E57-8B11-4A5C0E2D7B90}
AppName=LobsterVM
AppVersion={#AppVersion}
AppPublisher=LobsterOS
AppPublisherURL=https://github.com/jlaportebot/pi5-emulator
DefaultDirName={autopf}\LobsterVM
DefaultGroupName=LobsterVM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=LobsterVM-Setup-{#AppVersion}-x64
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\LobsterVM.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\LobsterVM"; Filename: "{app}\LobsterVM.exe"
Name: "{autodesktop}\LobsterVM"; Filename: "{app}\LobsterVM.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LobsterVM.exe"; Description: "Launch LobsterVM"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
