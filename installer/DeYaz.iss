#define MyAppName "DeYaz"
#define MyAppVersion "1.0.10"
#define MyAppPublisher "Ali Hasanov"
#define MyAppURL "https://github.com/hasan0v/deyaz"
#define MyAppSupportURL "https://github.com/hasan0v/deyaz/issues"
#define MyAppExeName "DeYaz.exe"

[Setup]
AppId={{C3DDB819-3838-4A02-9DD3-BD16C82C0279}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppCopyright=Copyright (C) 2026 Ali Hasanov
AppComments=Voice, file and meeting transcription for Windows.
AppContact={#MyAppSupportURL}
AppSupportURL={#MyAppSupportURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-installer
OutputBaseFilename=DeYaz-Setup-{#MyAppVersion}-x64
SetupIconFile=..\assets\deyaz.ico
WizardImageFile=..\assets\installer-wizard.bmp
WizardSmallImageFile=..\assets\installer-small.bmp
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\DeYaz\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\{#MyAppExeName}')) then
    Exit;

  { Works for every previous version and never waits on the GUI process. }
  Exec(ExpandConstant('{sys}\taskkill.exe'),
    '/F /T /IM "{#MyAppExeName}"', '', SW_HIDE, ewWaitUntilTerminated,
    ResultCode);
  Sleep(250);
end;

procedure InitializeWizard;
begin
  WizardForm.Caption := 'DeYaz ' + '{#MyAppVersion}' + ' Setup';
  WizardForm.WelcomeLabel1.Caption := 'Welcome to DeYaz';
  WizardForm.WelcomeLabel2.Caption :=
    'Install DeYaz for voice dictation, file transcription and meeting notes.' + #13#10#13#10 +
    'Created by Ali Hasanov' + #13#10 +
    '{#MyAppURL}';
  WizardForm.FinishedHeadingLabel.Caption := 'DeYaz is ready';
  WizardForm.FinishedLabel.Caption :=
    'Installation completed successfully. You can now launch DeYaz.';
end;
