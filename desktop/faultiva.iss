; Faultiva installer
;
; Builds Faultiva-Setup-<version>.exe from the PyInstaller output.  Installs
; per-user by default so no administrator prompt appears: the app writes
; characterization results under the user's profile, and asking for admin to
; install a diagnostic tool is a worse first impression than a UAC-free run.
;
; Nothing here bundles Yosys or Verilator.  Characterization needs them, the
; app detects them at runtime, and diagnosis works without them.

#define AppName       "Faultiva"
#define AppVersion    "1.0.0"
#define AppPublisher  "Pavan Nithin"
#define AppURL        "https://github.com/pavannithin224-abcd/faultiva"
#define AppExe        "Faultiva.exe"
; set FAULTIVA_DIST / FAULTIVA_OUT before compiling, or edit these
#ifndef SourceDir
  #define SourceDir GetEnv("FAULTIVA_DIST")
#endif
#if SourceDir == ""
  #define SourceDir "..\dist\Faultiva"
#endif
#ifndef OutputDir
  #define OutputDir GetEnv("FAULTIVA_OUT")
#endif
#if OutputDir == ""
  #define OutputDir "..\installer"
#endif

[Setup]
AppId={{7F3A9C21-5B4E-4D8A-9E16-FA2D7C08B3E5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Stuck-at fault detection and localization for digital circuits

; per-user install: no UAC prompt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes

LicenseFile={#SourceDir}\_internal\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=Faultiva-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\_internal\app\ui\faultiva.ico
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExe}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Characterize your own circuit (guide)"; \
  Filename: "{app}\_internal\docs\CHARACTERIZATION.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller leaves bytecode behind; remove it so the directory goes cleanly
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nFaultiva diagnoses stuck-at faults in digital circuits: it detects whether a chip is faulty, localizes the fault to a gate, and verifies the result with an independent model.%n%nFour circuits are included and ready to use. Characterizing your own circuit additionally needs Yosys and Verilator, which the app detects after installation.
