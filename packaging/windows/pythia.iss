; Inno Setup script for Pythia (2026-08-07). Per-user install, no admin
; required -- installs to {localappdata}\Programs\Pythia (same convention
; VS Code/Discord use), Start Menu shortcut, real uninstaller. Expects
; scripts/build_windows.py to have already produced dist\windows\pythia\
; (the PyInstaller onedir output) before this is compiled.
;
; Compile with Inno Setup's ISCC.exe from the repo root:
;   iscc packaging\windows\pythia.iss
;   iscc /DMyAppArch=arm64 packaging\windows\pythia.iss   (ARM64 build)
; Produces dist\windows\Pythia-<version>-win-<arch>-setup.exe
;
; MyAppArch defaults to x64 (real, existing single-arch builds keep working
; unchanged); CI passes /DMyAppArch=arm64 on the ARM64 runner (2026-08-07 --
; see .github/workflows/build.yml's windows job matrix).

#define MyAppName "Pythia"
#define MyAppVersion GetFileVersion("..\..\dist\windows\pythia\pythia.exe")
#define MyAppPublisher "Pythia Team"
#define MyAppURL "https://pythia.net/"
#define MyAppExeName "pythia.exe"
#ifndef MyAppArch
  #define MyAppArch "x64"
#endif
#if MyAppArch == "arm64"
  #define MyAppArchAllowed "arm64"
#else
  #define MyAppArchAllowed "x64compatible"
#endif

[Setup]
AppId={{9C7A6E9A-3F1B-4A3E-9C4D-2B6E0F7A1D5E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Per-user, no admin required (2026-08-07 plan: "installs per-user ...
; no admin needed"). DefaultDirName resolves under the current user's own
; LocalAppData, and PrivilegesRequired=lowest means UAC is never prompted.
DefaultDirName={localappdata}\Programs\Pythia
DefaultGroupName=Pythia
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\..\dist\windows
OutputBaseFilename=Pythia-{#MyAppVersion}-win-{#MyAppArch}-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed={#MyAppArchAllowed}
ArchitecturesInstallIn64BitMode={#MyAppArchAllowed}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\dist\windows\pythia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Pythia"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Pythia"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Pythia"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Pythia"; Flags: nowait postinstall skipifsilent
