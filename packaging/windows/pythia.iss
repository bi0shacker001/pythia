; Pythia's Windows installer (Inno Setup 6), over the standalone build packaging/pyinstaller/pythia.spec
; makes. Built by .github/workflows/build.yml:
;
;     iscc /DAppVersion=<version> /DSourceDir=<dist\standalone\Pythia> /DOutputDir=<dir> packaging\windows\pythia.iss
;
; Installs for the person running it by default (no administrator prompt; an update installs over it the
; same way), or for everyone on the computer when they pick that. A Start menu entry, an optional desktop
; icon, the pythia: link handler, and an uninstaller in Settings > Apps. Uninstalling leaves the person's
; library and settings (%APPDATA%\pythia, %LOCALAPPDATA%\pythia) where they are.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\standalone\Pythia"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
; Fixed for every release: Windows knows an update by it.
AppId={{88736559-EFA6-4752-A6A7-1FC3882F97C7}
AppName=Pythia
AppVersion={#AppVersion}
AppVerName=Pythia {#AppVersion}
AppPublisher=Pythia
AppPublisherURL=https://github.com/bi0shacker001/pythia
AppSupportURL=https://github.com/bi0shacker001/pythia/issues
DefaultDirName={autopf}\Pythia
DefaultGroupName=Pythia
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Pythia-{#AppVersion}-windows-x86_64-setup
SetupIconFile={#SourceDir}\..\pythia.ico
UninstallDisplayIcon={app}\Pythia.exe
UninstallDisplayName=Pythia
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; The previous version's bundle, so no module from it outlives the update.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Pythia"; Filename: "{app}\Pythia.exe"; Comment: "Game library manager for your computers and handhelds"
Name: "{autodesktop}\Pythia"; Filename: "{app}\Pythia.exe"; Tasks: desktopicon

[Registry]
; pythia: links (pythia:rungameid/<id>) open Pythia, as its Linux desktop entry says.
Root: HKA; Subkey: "Software\Classes\pythia"; ValueType: string; ValueName: ""; ValueData: "URL:Pythia"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\pythia"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\pythia\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\Pythia.exe"",0"
Root: HKA; Subkey: "Software\Classes\pythia\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Pythia.exe"" ""%1"""

[Run]
Filename: "{app}\Pythia.exe"; Description: "{cm:LaunchProgram,Pythia}"; Flags: nowait postinstall skipifsilent
