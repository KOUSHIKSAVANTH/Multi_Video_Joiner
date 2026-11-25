; Inno Setup script for Multi Video Joiner.
; Built by:  python scripts/build.py --installer
; Installs per user (no admin rights needed), adds a Start menu entry, an optional
; desktop icon, opens .mvj project files with the app, and registers an uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Multi Video Joiner"
#define AppExe "MultiVideoJoiner.exe"

[Setup]
AppId={{6F0E9C3B-5A1D-4C7E-9B2A-3D8E1F4A7C21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=KOUSHIKSAVANTH
AppPublisherURL=https://github.com/KOUSHIKSAVANTH/Multi_Video_Joiner
AppSupportURL=https://github.com/KOUSHIKSAVANTH/Multi_Video_Joiner/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=MultiVideoJoiner-Setup
SetupIconFile=..\mvjoiner\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assoc"; Description: "Open .mvj project files with {#AppName}"; GroupDescription: "File types:"

[Files]
Source: "..\dist\MultiVideoJoiner\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.mvj"; ValueType: string; ValueName: ""; ValueData: "MultiVideoJoiner.Project"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\MultiVideoJoiner.Project"; ValueType: string; ValueName: ""; ValueData: "{#AppName} project"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\MultiVideoJoiner.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\MultiVideoJoiner.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
