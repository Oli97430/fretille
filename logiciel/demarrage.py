"""
Frétille — lancement au démarrage de Windows.

Un raccourci « Fretille.lnk » dans le dossier Démarrage de l'utilisateur lance l'appli sans
console. Il est créé ou retiré depuis les Réglages ; aucun droit administrateur n'est demandé.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from i18n import t

ICI = Path(__file__).resolve().parent
NOM_RACCOURCI = "Fretille.lnk"
# raccourci d'EZVIZ Studio créé par son installateur, s'il existe
EZVIZ_MENU = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / \
    r"Microsoft\Windows\Start Menu\Programs\hicloud\Ezviz Studio\Ezviz Studio.lnk"


def dossier_demarrage():
    return Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def raccourci():
    return dossier_demarrage() / NOM_RACCOURCI


def programme_conseille():
    """Programme à ouvrir avant la surveillance : EZVIZ Studio s'il est installé."""
    return str(EZVIZ_MENU) if EZVIZ_MENU.exists() else ""


def _pythonw():
    candidat = Path(sys.executable).with_name("pythonw.exe")
    return candidat if candidat.exists() else Path(sys.executable)


def _powershell(script, **env):
    """env : valeurs lues par le script dans $env:NOM, jamais collées dans son texte."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=30, encoding="oem", errors="replace",
                       env={**os.environ, **env}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or t("raccourci impossible"))


def est_active():
    return raccourci().exists()


def activer(cible=None):
    """Crée le raccourci de démarrage : pythonw fretille_app.py --demarrage, dans ce dossier.
    cible : dossier de destination du raccourci (essais) ; par défaut le dossier Démarrage."""
    lien = Path(cible) / NOM_RACCOURCI if cible else raccourci()
    lien.parent.mkdir(parents=True, exist_ok=True)
    # chemins passés par l'environnement, jamais collés dans le script : une apostrophe
    # (même typographique, que PowerShell lit aussi comme guillemet) ne peut rien casser
    _powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BR_LIEN);"
        "$s.TargetPath = $env:BR_CIBLE;"
        "$s.Arguments = '\"' + $env:BR_SCRIPT + '\" --demarrage';"
        "$s.WorkingDirectory = $env:BR_DOSSIER;"
        "$s.IconLocation = $env:BR_ICONE;"
        "$s.Description = $env:BR_DESCRIPTION;"
        "$s.Save()",
        BR_LIEN=str(lien), BR_CIBLE=str(_pythonw()), BR_SCRIPT=str(ICI / "fretille_app.py"),
        BR_DOSSIER=str(ICI), BR_ICONE=str(ICI / "fretille.ico"),
        BR_DESCRIPTION=t("Frétille : surveillance des bacs"))
    return lien


def desactiver(cible=None):
    lien = Path(cible) / NOM_RACCOURCI if cible else raccourci()
    lien.unlink(missing_ok=True)


def lancer_programme(chemin, sans_admin=False):
    """Ouvre un programme ou un raccourci. sans_admin : même s'il demande les droits
    administrateur (EZVIZ Studio), il est lancé avec les droits ordinaires, comme Frétille ;
    sans quoi Windows interdit à Frétille de cliquer dedans pour relancer le direct.
    Un programme qui a vraiment besoin de ces droits peut alors mal fonctionner."""
    chemin = Path(chemin)
    if not sans_admin:
        os.startfile(chemin)
        return
    cible, arguments, dossier = chemin, "", chemin.parent
    if chemin.suffix.lower() == ".lnk":
        lu = lire_raccourci(chemin) + ("", "", "")
        cible, arguments, dossier = Path(lu[0]), lu[1], Path(lu[2] or Path(lu[0]).parent)
    env = {**os.environ, "__COMPAT_LAYER": "RunAsInvoker"}
    subprocess.Popen(f'"{cible}" {arguments}'.strip(), cwd=str(dossier), env=env,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def lire_raccourci(lien):
    """(cible, arguments, dossier) d'un raccourci, pour les essais."""
    # sortie en JSON échappé ASCII : ni page de code OEM, ni champ vide qui décale les autres
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BR_LIEN);"
                        "[Console]::Out.Write((ConvertTo-Json -Compress @($s.TargetPath, $s.Arguments, $s.WorkingDirectory))"
                        " -replace '[^\\x00-\\x7f]', { '\\u{0:x4}' -f [int][char]$args[0].Value })"],
                       capture_output=True, text=True, timeout=30, encoding="ascii", errors="replace",
                       env={**os.environ, "BR_LIEN": str(lien)},
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return tuple(str(v or "") for v in json.loads(r.stdout))
    except ValueError:
        return ()
