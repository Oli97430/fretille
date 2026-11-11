"""Ouverture des webcams : essais successifs (API, compression, taille), avec de fausses caméras.

  python tests/test_webcam.py
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sources  # noqa: E402

ok = 0


def verifier(nom, condition, detail=""):
    global ok
    if not condition:
        print(f"ÉCHEC {nom} {detail}")
        sys.exit(1)
    ok += 1
    print(f"OK    {nom}")


class FausseWebcam:
    """accepte(api, compression, largeur) -> None (ne s'ouvre pas), "rien", "noir" ou "image"."""
    ouvertes = 0

    def __init__(self, accepte, api, journal):
        self.accepte, self.api, self.journal = accepte, api, journal
        self.compression, self.largeur = None, None
        self.ouverte = accepte(api, None, None) is not None
        self.lues = 0
        if self.ouverte:
            FausseWebcam.ouvertes += 1

    def isOpened(self):
        return self.ouverte

    def set(self, propriete, valeur):
        if propriete == cv2.CAP_PROP_FOURCC:
            self.compression = "MJPG"
        elif propriete == cv2.CAP_PROP_FRAME_WIDTH:
            self.largeur = int(valeur)
        return True

    def read(self):
        self.lues += 1
        rendu = self.accepte(self.api, self.compression, self.largeur)
        if self.lues == 1:
            self.journal.append((self.api, self.compression, self.largeur, rendu))
        if rendu == "image" and self.lues > 2:          # deux images vides au démarrage
            return True, np.full((720, self.largeur or 640, 3), 90, np.uint8)
        if rendu == "noir":
            return True, np.zeros((480, 640, 3), np.uint8)
        return False, None

    def release(self):
        if self.ouverte:
            FausseWebcam.ouvertes -= 1
            self.ouverte = False


def essayer(accepte, numero=0):
    journal = []
    sources._essai_retenu.clear()
    camera, image = sources.ouvrir_webcam(numero, lambda n, api: FausseWebcam(accepte, api, journal))
    return camera, image, journal


# webcam de portable : tout marche du premier coup
camera, image, journal = essayer(lambda api, c, l: "image")
verifier("webcam ordinaire : 1080p MJPG dès le premier essai", image is not None and image.shape[1] == 1920 and len(journal) == 1)
camera.release()

# webcam USB qui s'ouvre mais ne rend rien en 1080p : on descend en 720p
camera, image, journal = essayer(lambda api, c, l: "rien" if l == 1920 else "image")
verifier("webcam sans 1080p : repli en 720p", image is not None and image.shape[1] == 1280, journal)
camera.release()

# webcam USB sans MJPG, muette sous Media Foundation : DirectShow sans compression
camera, image, journal = essayer(lambda api, c, l: "image" if api == cv2.CAP_DSHOW and c is None else "rien")
verifier("webcam sans MJPG : DirectShow sans compression", image is not None and camera.api == cv2.CAP_DSHOW, journal)
verifier("essai retenu pour les reconnexions", sources._essai_retenu[0] == (cv2.CAP_DSHOW, None, 1280, 720))
journal.clear()
camera.release()
camera, image = sources.ouvrir_webcam(0, lambda n, api: FausseWebcam(lambda a, c, l: "image" if a == cv2.CAP_DSHOW and c is None
                                                                      else "rien", api, journal))
verifier("reconnexion : l'essai retenu passe en premier", len(journal) == 1 and image is not None, journal)
camera.release()

# webcam qui ne marche qu'avec ses réglages d'origine
camera, image, journal = essayer(lambda api, c, l: "image" if c is None and l is None else "rien")
verifier("webcam capricieuse : réglages d'origine", image is not None and image.shape[1] == 640, journal)
camera.release()

# image noire en 1080p (format mal géré), bonne image en 720p : on préfère la bonne
camera, image, journal = essayer(lambda api, c, l: "noir" if l == 1920 else "image")
verifier("image noire écartée si un autre essai fait mieux", image is not None and image.mean() > 50, journal)
camera.release()

# cache fermé : noir partout, accepté faute de mieux
camera, image, journal = essayer(lambda api, c, l: "noir")
verifier("noir partout : accepté quand même", camera is not None and image.mean() == 0)
camera.release()

# rien du tout
camera, image, journal = essayer(lambda api, c, l: None)
verifier("webcam absente : (None, None)", camera is None and image is None)
camera, image, journal = essayer(lambda api, c, l: "rien")
verifier("webcam muette : (None, None)", camera is None and len(journal) == len(sources.ESSAIS_WEBCAM), journal)
verifier("toutes les caméras essayées sont refermées", FausseWebcam.ouvertes == 0, FausseWebcam.ouvertes)

# liste pour le choix sur aperçu : seules les webcams qui rendent une image
sources._essai_retenu.clear()
trouvees = sources.lister_webcams(4, lambda n, api: FausseWebcam((lambda a, c, l: "image") if n in (0, 2)
                                                                  else (lambda a, c, l: None), api, []))
verifier("liste des webcams : numéros 0 et 2", [n for n, _i in trouvees] == [0, 2] and FausseWebcam.ouvertes == 0, trouvees)
print(f"\n{ok} vérifications réussies")
