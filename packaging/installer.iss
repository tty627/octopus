#ifndef AppVersion
#define AppVersion "2.1.0.dev0"
#endif
#ifndef AppNumericVersion
#define AppNumericVersion "2.1.0.0"
#endif

[Setup]
AppId={{B858A43D-6D6A-43D8-8EA8-DF66A135A75B}
AppName=Octopus
AppVersion={#AppVersion}
AppVerName=Octopus {#AppVersion}
AppPublisher=Octopus
AppPublisherURL=https://github.com/tty627/octopus
AppSupportURL=https://github.com/tty627/octopus/issues
DefaultDirName={localappdata}\Programs\Octopus
DefaultGroupName=Octopus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir=..\release
OutputBaseFilename=Octopus-{#AppVersion}-win-x64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallDisplayIcon={app}\Octopus.exe
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductName=Octopus
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoProductTextVersion={#AppVersion}
#ifdef SignedBuild
SignTool=OctopusSign
SignedUninstaller=yes
#endif

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "..\dist\Octopus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "octopus.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Octopus"; Filename: "{app}\Octopus.exe"; WorkingDir: "{app}"
Name: "{group}\Octopus 命令行"; Filename: "{cmd}"; Parameters: "/K ""{app}\octopus-cli.exe"" --help"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Octopus.exe"; Description: "启动 Octopus 首次设置"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\octopus-cli.exe"; Parameters: "watch stop"; Flags: runhidden skipifdoesntexist
Filename: "{app}\octopus-cli.exe"; Parameters: "api stop"; Flags: runhidden skipifdoesntexist

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    SuppressibleMsgBox('Octopus 已卸载。您的原始资料、任务与 %APPDATA%\Octopus 配置均已保留。',
      mbInformation, MB_OK, IDOK);
end;
