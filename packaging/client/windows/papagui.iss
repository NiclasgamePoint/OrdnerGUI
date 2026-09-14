; Build through tools/build_installers.py; all paths are supplied by the builder.
[Setup]
AppId={{A789C903-AF7D-40AB-B8E3-74D25234D415}
AppName=PapaGUI
AppVersion={#AppVersion}
AppPublisher=PapaGUI contributors
AppPublisherURL=https://github.com/NiclasgamePoint/OrdnerGUI
DefaultDirName={localappdata}\Programs\PapaGUI
DefaultGroupName=PapaGUI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=papagui-client-{#AppVersion}-windows-x64-setup-unsigned
LicenseFile={#PayloadDir}\licenses\LICENSE
UninstallDisplayIcon={app}\papagui-client.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#PayloadDir}\papagui-client.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\papagui-tray.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PapaGUI"; Filename: "{app}\papagui-client.exe"
Name: "{group}\PapaGUI Indexserver-Verwaltung"; Filename: "{app}\papagui-tray.exe"
Name: "{group}\Lizenzen"; Filename: "{app}\licenses"

; User settings, offline data and server volumes are outside {app} and retained.
