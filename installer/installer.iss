; ============================================================
; Shared Inno Setup template for the JDE "Simple X Tools".
; Compiled in CI by Build-Tools/.github/workflows/release.yml; every per-app
; value arrives as an ISCC /D define (see that workflow's "Build installer" step):
;   MySrcRoot, MySourceDir, MyAppName, MyAppVersion, MyExeName,
;   MyAppId, MyRepo, MyIssues, MyOutput
;
; Per-user install by default (no admin); the user may choose all-users at the
; privileges prompt. Start-menu shortcut always created; desktop shortcut and
; launch-on-finish are pre-checked options. Shipped UNSIGNED by design
; (PolyForm-NC licensing blocks free code signing) - the SignTool hook below is
; left commented so signing can drop in later without other changes.
; ============================================================

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=JDE Projects
AppPublisherURL={#MyRepo}
AppSupportURL={#MyIssues}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyExeName}
SourceDir={#MySrcRoot}
OutputDir=.
OutputBaseFilename={#MyOutput}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

; --- Code signing intentionally disabled. To enable later, register a signer
;     (SignTool=...) in CI and uncomment these two lines:
; SignTool=mysigner
; SignedUninstaller=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#MySourceDir}\*";          DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "README.txt";                DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "LICENSE";                   DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "LICENSE.txt";               DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "THIRD-PARTY-LICENSES.txt";  DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyExeName}"
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyExeName}"; Tasks: desktopicon
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
