; Windows installer for LED Wall Creator (Inno Setup 6: https://jrsoftware.org/isinfo.php).
;
; Build the app first, then the installer (from the python folder):
;   pyinstaller --noconfirm LEDWallCreator.spec
;   iscc /DAppVersion=1.2.3 installer\LEDWallCreator.iss      -> dist\LEDWallCreator-Setup.exe
; (build_windows.bat in the repository root does both.)
;
; What it does, like any regular Windows program:
;   - installs into C:\Program Files\LED Wall Creator (asks for admin permission)
;   - Start menu entry, optional desktop shortcut (a checkbox in the wizard)
;   - opens .ledwall files with the app, with the app's icon
;   - shows up in Settings > Apps (and Control Panel > Programs) with its icon, version and an
;     Uninstall button; uninstalling removes the program, shortcuts and the file association.
;     The user's projects, videos and settings are never touched.
;   - running a newer Setup over an installed copy updates it in place (the app's own updater does
;     exactly that, see ledwall/updater.py run_installer)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "LED Wall Creator"
#define AppExe "LEDWallCreator.exe"
#define AppPublisher "LED Wall Creator"
#define AppURL "https://github.com/HenriqueSim/LED_WALL_CREATOR"
; must match APP_USER_MODEL_ID in ledwall/resources.py (taskbar grouping / icon)
#define AppUserModelID "LEDWallCreator.App"
#define ProgId "LEDWallCreator.Project"

[Setup]
; Never change AppId: Windows uses it to recognise the installed program for updates and uninstall.
AppId={{6F4E2B1A-8C3D-4F7E-9A5B-2D1C0E8F7A63}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SourceDir=..
OutputDir=dist
OutputBaseFilename=LEDWallCreator-Setup
SetupIconFile=icons\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; the whole PyInstaller build (dist\LEDWallCreator\LEDWallCreator.exe + _internal\)
Source: "dist\LEDWallCreator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files of the previous version that a newer build may no longer ship
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
; double-clicking a .ledwall file opens it in the app (removed again on uninstall)
Root: HKA; Subkey: "Software\Classes\.ledwall"; ValueType: string; ValueName: ""; ValueData: "{#ProgId}"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.ledwall\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "LED Wall Creator project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\_internal\icons\app.ico"",0"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".ledwall"; ValueData: ""; Flags: uninsdeletekey
; the zip version of the app registered .ledwall for the current user only; that entry would
; override this one (and point at the old folder), so it is removed
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; Flags: deletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\.ledwall"; Flags: deletekey dontcreatekey

[Run]
; last wizard page: "Launch LED Wall Creator" (started as the normal user, not as admin)
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; in-app update (Setup.exe /SILENT /relaunch=1 [/openfile="..."]): start the new version again
Filename: "{app}\{#AppExe}"; Parameters: """{param:openfile|}"""; Flags: nowait runasoriginaluser; Check: Relaunch

[Code]
function Relaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;
