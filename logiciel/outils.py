"""
Frétille — outils communs : images, fichiers écrits sans risque, journaux CSV, lecteur de caméra.

Repris de Broutille (même logique éprouvée) : écriture en deux temps, journal qui attend si
Excel le tient ouvert, lecteur de caméra dans son propre fil (une caméra débranchée bloque read()).
"""
import csv
import json
import queue
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from i18n import t
from sources import ouvrir_source


def sans_accents(texte):
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()


def nom_fichier(texte):
    return "".join(c if c.isalnum() else "_" for c in sans_accents(texte)).strip("_")


def ajuster(image, taille):
    """Met l'image à la taille (l, h) sans la déformer : mise à l'échelle, puis bandes noires."""
    l, h = taille
    e = min(l / image.shape[1], h / image.shape[0])
    nl, nh = max(1, round(image.shape[1] * e)), max(1, round(image.shape[0] * e))
    sortie = np.zeros((h, l, 3), np.uint8)
    x, y = (l - nl) // 2, (h - nh) // 2
    sortie[y:y + nh, x:x + nl] = cv2.resize(image, (nl, nh))
    return sortie


def lire_image(chemin):
    """Image BGR, ou None si le fichier est absent, vide ou abîmé."""
    try:
        return cv2.imdecode(np.fromfile(str(chemin), np.uint8), cv2.IMREAD_COLOR)
    except (OSError, ValueError, cv2.error):
        return None


def ecrire_image(chemin, image, qualite=92):
    """Écriture en deux temps : une coupure ne laisse jamais de .jpg à moitié écrit."""
    ok, tampon = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, qualite])
    if ok:
        tmp = Path(str(chemin) + ".part")
        tmp.write_bytes(tampon.tobytes())
        tmp.replace(chemin)
    return ok


def date_photo(img, chemin):
    """Date de prise de vue (EXIF DateTimeOriginal), sinon date du fichier."""
    try:
        brut = img.getexif().get_ifd(0x8769).get(0x9003) or img.getexif().get(0x0132)
        if isinstance(brut, bytes):
            brut = brut.decode("ascii", "ignore")
        if brut:
            return datetime.strptime(str(brut).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
    except (ValueError, KeyError, AttributeError, OSError):
        pass
    return datetime.fromtimestamp(Path(chemin).stat().st_mtime)


def ecrire_json(chemin, donnees):
    """JSON écrit en deux temps, avec une copie .bak de la version précédente si elle était saine."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if chemin.exists():
        try:
            json.loads(chemin.read_text(encoding="utf-8"))
            chemin.with_suffix(".bak").write_bytes(chemin.read_bytes())
        except (OSError, ValueError):
            pass                        # version actuelle abîmée : la copie .bak reste la bonne
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, indent=1, ensure_ascii=False), encoding="utf-8")
    insister(lambda: tmp.replace(chemin))


def lire_json(chemin, defaut):
    """Contenu du JSON, ou de sa copie .bak s'il est abîmé, ou `defaut`."""
    chemin = Path(chemin)
    for c in (chemin, chemin.with_suffix(".bak")):
        try:
            return json.loads(c.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return defaut


# ------------------------------------------------------------------
#  Journaux CSV
# ------------------------------------------------------------------
class FichierOccupe(Exception):
    """Un journal est bloqué, en général ouvert dans Excel."""


VERROU_JOURNAUX = threading.RLock()


def insister(action, essais=6):
    """Windows refuse un fichier ouvert ailleurs, parfois juste un instant (antivirus, Excel)."""
    for i in range(essais):
        try:
            return action()
        except PermissionError:
            if i == essais - 1:
                raise FichierOccupe(t("un journal est ouvert dans un autre programme : fermez-le, puis réessayez")) from None
            time.sleep(0.25)


def lire_csv(chemin, colonnes_obligatoires=()):
    """Lignes d'un journal ; tolère Excel (autre encodage, champs de fin ôtés) et une ligne coupée."""
    chemin = Path(chemin)
    if not chemin.exists():
        return []
    brut = insister(chemin.read_bytes)
    textes = []
    for ligne in brut.removeprefix(b"\xef\xbb\xbf").splitlines():
        try:
            textes.append(ligne.decode("utf-8"))
        except UnicodeDecodeError:
            textes.append(ligne.decode("cp1252", "replace"))
    lignes = []
    for l in csv.DictReader(textes, delimiter=";", restval=""):
        l.pop(None, None)
        if any(not l.get(c) for c in colonnes_obligatoires):
            continue                    # ligne coupée par une panne
        lignes.append({c: (v if v is not None else "") for c, v in l.items()})
    return lignes


def ajouter_csv(chemin, colonnes, ligne):
    """Ajoute une ligne ; si le fichier est bloqué, elle attend dans <nom>.attente.jsonl et sera
    écrite au prochain ajout. Renvoie True si elle est déjà dans le journal."""
    chemin = Path(chemin)
    attente = chemin.with_suffix(".attente.jsonl")
    with VERROU_JOURNAUX:
        en_attente = []
        if attente.exists():
            for l in attente.read_text(encoding="utf-8").splitlines():
                try:
                    en_attente.append(json.loads(l))
                except ValueError:
                    pass
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            if chemin.exists():
                with open(chemin, encoding="utf-8-sig", errors="replace") as f:
                    entete = f.readline().strip().split(";")
                if entete != list(colonnes):        # autre version : mis de côté, rien n'est mélangé
                    ancien, n = chemin.with_name(chemin.stem + ".ancien.csv"), 1
                    while ancien.exists():
                        n += 1
                        ancien = chemin.with_name(f"{chemin.stem}.ancien{n}.csv")
                    insister(lambda: chemin.replace(ancien))
            nouveau = not chemin.exists()

            def ecrire():
                with open(chemin, "a", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", extrasaction="ignore")
                    if nouveau:
                        w.writeheader()
                    for l in en_attente + [ligne]:
                        w.writerow(l)
            insister(ecrire)
            attente.unlink(missing_ok=True)
            return True
        except FichierOccupe:
            with open(attente, "a", encoding="utf-8") as f:
                f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
            return False


# ------------------------------------------------------------------
#  Lecteur de caméra
# ------------------------------------------------------------------
class LecteurCamera(threading.Thread):
    """Lit la source dans son propre fil : une caméra débranchée ou un flux qui se fige bloquent
    read() sans limite. Mesure aussi les vraies images par seconde.
    insister : relancé après un gel, réessaie sans fin au lieu d'abandonner au premier échec."""

    def __init__(self, source, sur_message, insister=False):
        super().__init__(daemon=True)
        self.source, self.sur_message, self.insister = source, sur_message, insister
        self.file = queue.Queue(maxsize=60)
        self.pret, self.fin = threading.Event(), threading.Event()
        self.echec = False
        self.temps = deque(maxlen=90)
        self.fps_annonce = 15.0
        self.derniere = time.time()
        self.en_lecture = False

    def fps(self):
        if len(self.temps) >= 10 and self.temps[-1] > self.temps[0]:
            return float(np.clip((len(self.temps) - 1) / (self.temps[-1] - self.temps[0]), 1, 60))
        return self.fps_annonce

    def _deposer(self, image):
        maintenant = time.time()
        self.derniere = maintenant
        self.temps.append(maintenant)
        try:
            self.file.put_nowait((image, maintenant))
        except queue.Full:              # la surveillance a pris du retard : on perd l'image la plus vieille
            try:
                self.file.get_nowait()
            except queue.Empty:
                pass
            self.file.put_nowait((image, maintenant))

    def run(self):
        premiere = not self.insister
        while not self.fin.is_set():
            camera, image = ouvrir_source(self.source)
            if camera is None:
                if premiere:
                    self.echec = True
                    self.pret.set()
                    return
                self.fin.wait(5)
                continue
            try:
                annonce = camera.get(cv2.CAP_PROP_FPS)
                self.fps_annonce = annonce if 1 < annonce < 120 else 15.0
                self._deposer(image)
                premiere = False
                self.pret.set()
                while not self.fin.is_set():
                    self.en_lecture = True
                    ok, image = camera.read()
                    self.en_lecture = False
                    if not ok or image is None:
                        break
                    self._deposer(image)
            finally:
                self.en_lecture = False
                camera.release()
            if not self.fin.is_set():
                self.sur_message(t("Source interrompue, nouvelle tentative…"))
                self.fin.wait(2)
