; ============================================================
; Shared Inno Setup template for the JDE "Simple X Tools".
; Compiled in CI by Build-Tools/.github/workflows/release.yml; every per-app
; value arrives as an ISCC /D define (see that workflow's "Build installer" step):
;   MySrcRoot, MySourceDir, MyAppName, MyAppVersion, MyExeName,
;   MyAppId, MyRepo, MyIssues, MyOutput
;
; Per-user only: installed in %LocalAppData%\Programs without admin because apps
; write runtime data beside the exe. An older all-users copy can be migrated and removed.
; Start-menu shortcut always created; desktop shortcut and
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
DefaultDirName={userpf}\{#MyAppName}
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
Source: "LICENSE.LGPL-3.0.txt";      DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "LICENSE.GPL-3.0.txt";       DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[InstallDelete]
; Remove libraries that a newer build no longer ships, which otherwise stay forever.
; Apps keep all user data next to the exe and never inside _internal. This delete is
; not undone if installation fails or is cancelled partway; running Setup again repairs it.
Type: filesandordirs; Name: "{app}\_internal"; Check: IsUpgradeOfThisApp

[Icons]
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyExeName}"
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyExeName}"; Tasks: desktopicon
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  OldAllUsersAppDir, OldAllUsersUninstaller: String;
  CopyOldAllUsersData: Boolean;

function IsDataItem(const ItemName: String; Attributes: LongWord): Boolean;
begin
  Result := (ItemName <> '.') and (ItemName <> '..');
  if not Result then
    exit;
  if ((Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0)
    and (CompareText(ItemName, '_internal') = 0) then
  begin
    Result := False;
    exit;
  end;
  if CompareText(ItemName, '{#MyExeName}') = 0 then
  begin
    Result := False;
    exit;
  end;
  if CompareText(Copy(ItemName, 1, 5), 'unins') = 0 then
  begin
    Result := False;
    exit;
  end;
  if (CompareText(ItemName, 'README.txt') = 0)
    or (CompareText(ItemName, 'LICENSE') = 0)
    or (CompareText(ItemName, 'LICENSE.txt') = 0)
    or (CompareText(ItemName, 'THIRD-PARTY-LICENSES.txt') = 0)
    or (CompareText(ItemName, 'LICENSE.LGPL-3.0.txt') = 0)
    or (CompareText(ItemName, 'LICENSE.GPL-3.0.txt') = 0) then
    Result := False;
end;

function FindOldAllUsersInstall(var AppDir, Uninstaller: String): Boolean;
var
  UninstallKey: String;
begin
  Result := False;
  AppDir := '';
  Uninstaller := '';
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';
  if not RegKeyExists(HKEY_LOCAL_MACHINE, UninstallKey) then
  begin
    Log('Old all-users install not found: HKLM uninstall key was not found.');
    exit;
  end;
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, UninstallKey,
       'Inno Setup: App Path', AppDir) then
  begin
    Log('Old all-users install not found: HKLM app path value was not found.');
    exit;
  end;
  if not DirExists(AppDir) then
  begin
    Log('Old all-users install not found: app folder does not exist: ' + AppDir);
    exit;
  end;
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, UninstallKey,
       'UninstallString', Uninstaller) then
    Log('Old all-users install uninstaller value was not found: ' + AppDir)
  else
    Log('Old all-users install found: ' + AppDir);
  Result := True;
end;

function FolderHasDataItems(const Folder: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if FindFirst(Folder + '\*', FindRec) then
  try
    repeat
      if IsDataItem(FindRec.Name, FindRec.Attributes) then
      begin
        Result := True;
        exit;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure AddItem(var Items: TArrayOfString; const Item: String);
var
  ItemCount: Integer;
begin
  ItemCount := GetArrayLength(Items);
  SetArrayLength(Items, ItemCount + 1);
  Items[ItemCount] := Item;
end;

function CopyDataDirectory(const SourceDir, DestinationDir: String;
  var CreatedFiles, CreatedDirs: TArrayOfString): Boolean;
var
  FindRec: TFindRec;
  SourceItem, DestinationItem: String;
begin
  Result := True;
  { Even an empty folder lists '.' and '..', so a failed FindFirst means the
    folder could not be read. }
  if not FindFirst(SourceDir + '\*', FindRec) then
  begin
    Log('Could not read directory: ' + SourceDir);
    Result := False;
    exit;
  end;
  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        continue;
      SourceItem := SourceDir + '\' + FindRec.Name;
      DestinationItem := DestinationDir + '\' + FindRec.Name;
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        if not CreateDir(DestinationItem) then
        begin
          Log('Could not copy directory: ' + SourceItem);
          Result := False;
          exit;
        end;
        AddItem(CreatedDirs, DestinationItem);
        Log('Copied directory: ' + SourceItem + ' to ' + DestinationItem);
        if not CopyDataDirectory(SourceItem, DestinationItem, CreatedFiles, CreatedDirs) then
        begin
          Result := False;
          exit;
        end;
      end
      else
      begin
        if not CopyFile(SourceItem, DestinationItem, True) then
        begin
          Log('Could not copy file: ' + SourceItem);
          Result := False;
          exit;
        end;
        AddItem(CreatedFiles, DestinationItem);
        Log('Copied file: ' + SourceItem + ' to ' + DestinationItem);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function CopyDataItem(const SourceItem, DestinationItem: String; Attributes: LongWord;
  var CreatedFiles, CreatedDirs: TArrayOfString): Boolean;
begin
  if (Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
  begin
    Result := CreateDir(DestinationItem);
    if Result then
    begin
      AddItem(CreatedDirs, DestinationItem);
      Log('Copied directory: ' + SourceItem + ' to ' + DestinationItem);
      Result := CopyDataDirectory(SourceItem, DestinationItem, CreatedFiles, CreatedDirs);
    end
    else
      Log('Could not copy directory: ' + SourceItem);
  end
  else
  begin
    Result := CopyFile(SourceItem, DestinationItem, True);
    if Result then
    begin
      AddItem(CreatedFiles, DestinationItem);
      Log('Copied file: ' + SourceItem + ' to ' + DestinationItem);
    end
    else
      Log('Could not copy file: ' + SourceItem);
  end;
end;

procedure RemoveCopiedItems(const CreatedFiles, CreatedDirs: TArrayOfString);
var
  Index: Integer;
begin
  for Index := GetArrayLength(CreatedFiles) - 1 downto 0 do
    if DeleteFile(CreatedFiles[Index]) then
      Log('Removed copied file: ' + CreatedFiles[Index])
    else
      Log('Could not remove copied file: ' + CreatedFiles[Index]);
  for Index := GetArrayLength(CreatedDirs) - 1 downto 0 do
    if RemoveDir(CreatedDirs[Index]) then
      Log('Removed copied directory: ' + CreatedDirs[Index])
    else
      Log('Could not remove copied directory: ' + CreatedDirs[Index]);
end;

function BuildFailedItemsList(const FailedItems: TArrayOfString): String;
var
  Index, ItemCount: Integer;
begin
  Result := '';
  ItemCount := GetArrayLength(FailedItems);
  for Index := 0 to ItemCount - 1 do
  begin
    if Index = 10 then
      break;
    if Result <> '' then
      Result := Result + #13#10;
    Result := Result + FailedItems[Index];
  end;
  if ItemCount > 10 then
    Result := Result + #13#10 + 'and ' + IntToStr(ItemCount - 10) + ' more';
end;

procedure ShowOldCopyNotRemoved;
var
  MessageText: String;
begin
  MessageText := 'The older copy was not removed. You can remove it later from Settings > Apps > Installed apps.'
    + #13#10 + #13#10 + 'It is in:' + #13#10 + OldAllUsersAppDir;
  Log(MessageText);
  SuppressibleMsgBox(MessageText, mbInformation, MB_OK, IDOK);
end;

function StripSurroundingQuotes(const Value: String): String;
begin
  Result := Value;
  if (Length(Result) >= 2) and (Copy(Result, 1, 1) = '"')
    and (Copy(Result, Length(Result), 1) = '"') then
  begin
    Delete(Result, 1, 1);
    Delete(Result, Length(Result), 1);
  end;
end;

procedure RemoveOldAllUsersInstall;
var
  ErrorCode: Integer;
  Uninstaller: String;
begin
  Uninstaller := StripSurroundingQuotes(OldAllUsersUninstaller);
  if (Uninstaller = '') or not FileExists(Uninstaller) then
  begin
    Log('Could not start old all-users uninstaller: file was not found: ' + Uninstaller);
    ShowOldCopyNotRemoved;
    exit;
  end;
  if ShellExec('runas', Uninstaller, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode) then
    Log('Started old all-users uninstaller: ' + Uninstaller)
  else
  begin
    Log('Could not start old all-users uninstaller. Error code: ' + IntToStr(ErrorCode));
    ShowOldCopyNotRemoved;
  end;
end;

function InitializeSetup: Boolean;
var
  MessageText: String;
  Response: Integer;
begin
  Result := True;
  CopyOldAllUsersData := False;
  if not FindOldAllUsersInstall(OldAllUsersAppDir, OldAllUsersUninstaller) then
    exit;
  if WizardSilent then
  begin
    Log('Silent setup: old all-users install found and left alone: ' + OldAllUsersAppDir);
    exit;
  end;
  MessageText := 'An older copy of {#MyAppName} is installed for all users in:'
    + #13#10 + OldAllUsersAppDir + #13#10 + #13#10
    + 'This version installs just for you, in its own folder. Your settings and data are in the older copy''s folder.'
    + #13#10 + #13#10 + 'Copy your settings and data into the new install?'
    + #13#10 + #13#10 + 'Yes: copy them.' + #13#10
    + 'No: start fresh and leave the older copy as it is.' + #13#10
    + 'Cancel: stop Setup.';
  Log(MessageText);
  Response := SuppressibleMsgBox(MessageText, mbConfirmation, MB_YESNOCANCEL, IDYES);
  if Response = IDCANCEL then
  begin
    Log('User cancelled setup instead of migrating the old all-users install.');
    Result := False;
  end
  else if Response = IDYES then
    CopyOldAllUsersData := True
  else
    Log('User chose not to copy data from the old all-users install.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, SourceItem, DestinationItem, MessageText: String;
  FindRec: TFindRec;
  CreatedFiles, CreatedDirs, FailedItems: TArrayOfString;
  Response: Integer;
begin
  if (CurStep <> ssPostInstall) or not CopyOldAllUsersData then
    exit;
  AppDir := ExpandConstant('{app}');
  if FolderHasDataItems(AppDir) then
  begin
    MessageText := '{#MyAppName} already has settings and data in:' + #13#10 + AppDir
      + #13#10 + #13#10 + 'Nothing was copied, so they were not overwritten. The older copy''s data is still in:'
      + #13#10 + OldAllUsersAppDir;
    Log(MessageText);
    SuppressibleMsgBox(MessageText, mbInformation, MB_OK, IDOK);
    exit;
  end;
  if not FindFirst(OldAllUsersAppDir + '\*', FindRec) then
  begin
    Log('Could not read directory: ' + OldAllUsersAppDir);
    AddItem(FailedItems, OldAllUsersAppDir);
  end
  else
  try
    repeat
      if not IsDataItem(FindRec.Name, FindRec.Attributes) then
        continue;
      SourceItem := OldAllUsersAppDir + '\' + FindRec.Name;
      DestinationItem := AppDir + '\' + FindRec.Name;
      if not CopyDataItem(SourceItem, DestinationItem, FindRec.Attributes,
           CreatedFiles, CreatedDirs) then
        AddItem(FailedItems, FindRec.Name);
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
  if GetArrayLength(FailedItems) > 0 then
  begin
    RemoveCopiedItems(CreatedFiles, CreatedDirs);
    MessageText := 'Could not copy your settings and data from the older copy. Nothing was copied, and the older copy is unchanged in:'
      + #13#10 + OldAllUsersAppDir + #13#10 + #13#10
      + 'Items that could not be copied:' + #13#10 + BuildFailedItemsList(FailedItems)
      + #13#10 + #13#10 + 'To move them yourself, close {#MyAppName}, then copy them into:'
      + #13#10 + AppDir;
    Log(MessageText);
    SuppressibleMsgBox(MessageText, mbError, MB_OK, IDOK);
    exit;
  end;
  MessageText := 'Your settings and data were copied into:' + #13#10 + AppDir
    + #13#10 + #13#10 + 'Remove the older all-users copy now? Windows will ask for administrator approval.'
    + #13#10 + #13#10 + 'If it asks whether to also remove its settings and data, Yes is safe: your copy is in the new folder.';
  Log(MessageText);
  Response := SuppressibleMsgBox(MessageText, mbConfirmation, MB_YESNO, IDYES);
  if Response = IDYES then
    RemoveOldAllUsersInstall
  else
    ShowOldCopyNotRemoved;
end;

{ The app writes its runtime files (config, keys, logs, any data folders) next
  to the exe, so Inno's uninstaller leaves them behind. After the normal
  uninstall, offer to remove anything still in the install folder. Log each
  deletion failure, and if anything remains, report the actual entries the
  user must remove by hand.

  We delete every leftover EXCEPT the uninstaller's own files (unins*). By this
  step Inno has already removed its own files (it runs from a temp copy) and has
  already ATTEMPTED to remove the install folder -- but that attempt ran before
  this code and failed because our leftovers were still present, and Inno does
  not retry. So once we have emptied the folder we remove it ourselves. RemoveDir
  only deletes an EMPTY directory, so if the user keeps their data (or anything
  else remains) both the data and the folder stay put. }
procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  AppDir, Item, LeftoverNames, MessageText: string;
  LeftoverCount: Integer;
  FindRec: TFindRec;
begin
  if CurStep <> usPostUninstall then
    exit;
  AppDir := ExpandConstant('{app}');
  if not DirExists(AppDir) then
    exit;
  { A silent uninstall (/SILENT or /VERYSILENT, which is how WinGet and the
    recorded quiet uninstall command run it) never stops to ask: it keeps the
    user's data. }
  if UninstallSilent then
  begin
    Log('Silent uninstall: keeping settings and data in ' + AppDir);
    exit;
  end;
  if SuppressibleMsgBox(
       'Also remove all settings and data {#MyAppName} created in its'
       + ' install folder (configuration, keys, logs, and any'
       + ' files it saved there)?',
       mbConfirmation, MB_YESNO, IDNO) <> IDYES then
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
      begin
        if not DelTree(Item, True, True, True) then
          Log('Could not remove directory: ' + Item);
      end
      else
      begin
        if not DeleteFile(Item) then
          Log('Could not remove file: ' + Item);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;

  { Now that the leftovers are gone, remove the empty install folder that Inno
    left behind (see note above). No-op if anything still remains. }
  RemoveDir(AppDir);
  if not DirExists(AppDir) then
    exit;

  LeftoverNames := '';
  LeftoverCount := 0;
  if FindFirst(AppDir + '\*', FindRec) then
  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        continue;
      LeftoverCount := LeftoverCount + 1;
      Log('Uninstall cleanup left: ' + AppDir + '\' + FindRec.Name);
      if LeftoverCount <= 10 then
      begin
        if LeftoverNames <> '' then
          LeftoverNames := LeftoverNames + #13#10;
        LeftoverNames := LeftoverNames + FindRec.Name;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;

  MessageText := 'Could not remove the install folder:' + #13#10 + AppDir;
  if LeftoverNames <> '' then
    MessageText := MessageText + #13#10 + #13#10 + 'Items still there:'
      + #13#10 + LeftoverNames;
  if LeftoverCount > 10 then
    MessageText := MessageText + #13#10 + 'and ' + IntToStr(LeftoverCount - 10)
      + ' more';
  MessageText := MessageText + #13#10 + #13#10
    + 'Delete this folder by hand. Close the app first, or restart Windows if needed.';
  Log(MessageText);
  SuppressibleMsgBox(MessageText, mbError, MB_OK, IDOK);
end;

function PathsMatch(const FirstPath, SecondPath: String): Boolean;
var
  NormalizedFirstPath, NormalizedSecondPath: String;
begin
  NormalizedFirstPath := FirstPath;
  NormalizedSecondPath := SecondPath;
  while (Length(NormalizedFirstPath) > 0)
    and (Copy(NormalizedFirstPath, Length(NormalizedFirstPath), 1) = '\') do
    Delete(NormalizedFirstPath, Length(NormalizedFirstPath), 1);
  while (Length(NormalizedSecondPath) > 0)
    and (Copy(NormalizedSecondPath, Length(NormalizedSecondPath), 1) = '\') do
    Delete(NormalizedSecondPath, Length(NormalizedSecondPath), 1);
  Result := CompareText(NormalizedFirstPath, NormalizedSecondPath) = 0;
end;

function IsUpgradeOfThisApp: Boolean;
var
  AppDir, PreviousAppDir, UninstallKey: String;
begin
  Result := False;
  AppDir := ExpandConstant('{app}');
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';

  if RegQueryStringValue(HKEY_CURRENT_USER, UninstallKey,
       'Inno Setup: App Path', PreviousAppDir) then
  begin
    if PathsMatch(PreviousAppDir, AppDir) then
    begin
      Log('Upgrade cleanup enabled: HKCU uninstall entry matches ' + AppDir);
      Result := True;
      exit;
    end;
    Log('Upgrade cleanup skipped: HKCU uninstall entry points to ' + PreviousAppDir);
  end
  else
    Log('Upgrade cleanup: HKCU uninstall entry or app path value was not found.');
end;
