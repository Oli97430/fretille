"""
Frétille — alertes sur le téléphone, même hors de la maison, par ntfy (appli gratuite,
Android et iPhone, sans compte).

Le téléphone s'abonne à un « sujet » : un nom long et tiré au hasard, connu seulement de
l'application et du téléphone (le QR code le contient). Frétille y publie :
  - un prédateur au bassin (héron, chat…), avec sa photo ;
  - un poisson immobile en surface ; des poissons massés sous la surface (oxygène) ;
  - des poissons non vus, apathiques ou agités (sante.py) ; une eau hors normes (eau.py) ;
  - l'image du direct est figée ;
  - la surveillance s'est arrêtée sur une erreur.

Chaque alerte part une seule fois par épisode (alertes.json dans le dossier des visites) :
pas de rafale à chaque vérification ni après un redémarrage.
"""
import json
import secrets
import threading
import urllib.request
from pathlib import Path

from i18n import t

SERVEUR = "https://ntfy.sh"
FICHIER = "alertes.json"


def nouveau_sujet():
    """Nom de sujet impossible à deviner : c'est lui qui protège les alertes."""
    return "fretille-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "y")


def adresse_abonnement(serveur, sujet):
    return f"{serveur.rstrip('/')}/{sujet}"


def envoyer(serveur, sujet, titre, message, priorite=3, etiquettes=("fish",), delai=10):
    """Publie une notification ; lève OSError si le serveur ne répond pas."""
    corps = json.dumps({"topic": sujet, "title": titre, "message": message, "priority": int(priorite),
                        "tags": list(etiquettes)}).encode("utf-8")
    req = urllib.request.Request(serveur.rstrip("/") + "/", data=corps, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Fretille"})
    with urllib.request.urlopen(req, timeout=delai) as rep:
        if rep.status >= 300:
            raise OSError(t("ntfy : réponse {code}", code=rep.status))


class Alertes:
    """Envoi en arrière-plan, une fois par épisode. sur_erreur(texte) : appelé si l'envoi échoue."""

    def __init__(self, dossier, serveur, sujet, sur_erreur=lambda t: None, envoi=envoyer):
        self.dossier, self.serveur, self.sujet = Path(dossier), serveur, sujet
        self.sur_erreur, self.envoi = sur_erreur, envoi
        self._verrou = threading.Lock()

    @property
    def chemin(self):
        return self.dossier / FICHIER

    def _deja(self):
        try:
            return json.loads(self.chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _retenir(self, deja):
        tmp = self.chemin.with_suffix(".tmp")
        tmp.write_text(json.dumps(deja, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.chemin)

    def signaler(self, episode, titre, message, priorite=3, etiquettes=("fish",)):
        """Envoie si cet épisode n'a pas encore été signalé ; renvoie True si envoyé."""
        with self._verrou:
            deja = self._deja()
            sujet_alerte = episode.rsplit(":", 1)[0]       # « absence:t123 » : un seul épisode retenu par sujet
            if deja.get(sujet_alerte) == episode:
                return False
            try:
                self.envoi(self.serveur, self.sujet, titre, message, priorite, etiquettes)
            except OSError as e:
                self.sur_erreur(t("Alerte non envoyée : {erreur}", erreur=e))
                return False
            deja[sujet_alerte] = episode
            self._retenir(deja)
            return True

    def verifier(self, dues):
        """dues : [(épisode, titre, message, priorité)] ; renvoie les épisodes envoyés."""
        return [ep for ep, titre, message, priorite in dues if self.signaler(ep, titre, message, priorite, ("fish",))]

    def en_fond(self, fonction, *args):
        threading.Thread(target=fonction, args=args, daemon=True).start()
