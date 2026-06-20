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
Name: "desktopicon"; Description: "Create a desktop shortcut"

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

[Code]
{ The app writes its runtime files (config, keys, logs, any data folders) next
  to the exe, so Inno's uninstaller leaves them behind. After the normal
  uninstall, offer to remove anything still in the install folder.

  We delete every leftover EXCEPT the uninstaller's own files (unins*). By this
  step Inno has already removed its own files (it runs from a temp copy) and has
  already ATTEMPTED to remove the install folder -- but that attempt ran before
  this code and failed because our leftovers were still present, and Inno does
  not retry. So once we have emptied the folder we remove it ourselves. RemoveDir
  only deletes an EMPTY directory, so if the user keeps their data (or anything
  else remains) both the data and the folder stay put. }
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  AppDir, Item: string;
  FindRec: TFindRec;
begin
  if CurStep <> usPostUninstall then
    exit;
  AppDir := ExpandConstant('{app}');
  if not DirExists(AppDir) then
    exit;
  if MsgBox('Also remove all settings and data {#MyAppName} created in its'
            + #13#10 + 'install folder (configuration, keys, logs, and any'
            + ' files it saved there)?',
            mbConfirmation, MB_YESNO) <> IDYES then
    exit;

  if FindFirst(AppDir + '\*', FindRec) then
  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        continue;
      if CompareText(Copy(FindRec.Name, 1, 5), 'unins') = 0 then
        continue;                              { leave the running uninstaller }
      Item := AppDir + '\' + FindRec.Name;
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        DelTree(Item, True, True, True)
      else
        DeleteFile(Item);
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;

  { Now that the leftovers are gone, remove the empty install folder that Inno
    left behind (see note above). No-op if anything still remains. }
  RemoveDir(AppDir);
end;
