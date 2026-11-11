; Frétille — installateur en un clic (Inno Setup 6).
; Compiler :  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installateur\fretille.iss
; Résultat :  installateur\Fretille-Setup.exe
;
; Sans droits administrateur : tout va dans le profil de l'utilisateur. L'installateur copie
; l'application, puis lance installer.ps1 qui prépare Python et les bibliothèques (environ 3 Go
; téléchargés). Les données de l'utilisateur (Vidéos\Fretille, réglages) ne sont jamais touchées,
; ni à la mise à jour ni à la désinstallation.

#define Version "1.0.1"

[Setup]
AppId={{6F1B6C5E-2B0A-4F3E-9C55-FRETILLE0001}
AppName=Frétille
AppVersion={#Version}
AppPublisher=Frétille
AppPublisherURL=https://github.com/Oli97430/fretille
DefaultDirName={localappdata}\Programs\Fretille
DefaultGroupName=Frétille
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=Fretille-Setup
SetupIconFile=..\logiciel\fretille.ico
UninstallDisplayIcon={app}\fretille.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; un clic : pas de pages à valider, l'installation démarre aussitôt
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "nl"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "th"; MessagesFile: "compiler:Languages\Thai.isl"

[CustomMessages]
Preparation=Downloading Python and the libraries (about 3 GB). This takes 5 to 30 minutes: keep the black window open.
fr.Preparation=Téléchargement de Python et des bibliothèques (environ 3 Go). Comptez 5 à 30 minutes : laissez la fenêtre noire ouverte.
Echec=The components could not be installed. Check your Internet connection, then run the installer again.%n%nDetails: %1
fr.Echec=Les composants n'ont pas pu être installés. Vérifiez la connexion Internet, puis relancez l'installateur.%n%nDétails : %1
Lancer=Start Frétille
fr.Lancer=Lancer Frétille

[Files]
Source: "..\logiciel\*.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\logiciel\langues\*.json"; DestDir: "{app}\langues"; Excludes: "_cles.json"; Flags: ignoreversion
Source: "..\logiciel\fretille.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\logiciel\fretille.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\logiciel\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.fr.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\GUIDE.en.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\Frétille"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\fretille_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\fretille.ico"
Name: "{group}\Frétille"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\fretille_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\fretille.ico"
Name: "{group}\Guide (français)"; Filename: "{app}\GUIDE.md"
Name: "{group}\User guide (English)"; Filename: "{app}\GUIDE.en.md"

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\fretille_app.py"""; WorkingDir: "{app}"; Description: "{cm:Lancer}"; Flags: nowait postinstall skipifsilent; Check: Reussi

[UninstallDelete]
; l'environnement et les fichiers créés après l'installation ; jamais les données de l'utilisateur
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\hf_cache"
Type: files; Name: "{app}\installation.log"

[Code]
var
  Echoue: Boolean;

function Reussi: Boolean;
begin
  Result := not Echoue;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Journal: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := CustomMessage('Preparation');
    WizardForm.StatusLabel.WordWrap := True;
    WizardForm.StatusLabel.Height := ScaleY(60);
    Journal := ExpandConstant('{app}\installation.log');
    if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
                 '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer.ps1') + '" -Dossier "' + ExpandConstant('{app}') + '"',
                 ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, Code)) or (Code <> 0) then
    begin
      Echoue := True;
      MsgBox(FmtMessage(CustomMessage('Echec'), [Journal]), mbError, MB_OK);
    end;
  end;
end;
