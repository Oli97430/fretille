"""Moteur de bout en bout sur une vidéo fabriquée, avec un faux détecteur (couleurs) : comptage,
mesures minute, poisson immobile en surface, héron au bassin (photo + clip), poisson nommé sur
la vidéo (galerie enrichie, taille mesurée). Lancer : python tests/test_moteur.py"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
import numpy as np

import fretille_cam as m
import registre as reg

ok = 0


def verifier(nom, condition, detail=""):
    global ok
    print(("OK   " if condition else "ÉCHEC") + f" {nom} {detail}")
    if not condition:
        raise SystemExit(1)
    ok += 1


L, H, FPS, DUREE = 960, 540, 10, 70


def fabriquer(chemin):
    e = cv2.VideoWriter(str(chemin), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (L, H))
    fond = np.full((H, L, 3), (90, 110, 40), np.uint8)
    fond = cv2.add(fond, np.random.default_rng(1).integers(0, 25, (H, L, 3), dtype=np.uint8))
    for i in range(FPS * DUREE):
        t = i / FPS
        im = fond.copy()
        for k in range(4):                          # quatre poissons orange qui nagent
            x = int(100 + (k * 180 + t * 40) % 700)
            cv2.ellipse(im, (x, 200 + k * 70), (45, 18), 0, 0, 360, (0, 120, 255), -1)
        cv2.ellipse(im, (800, 70), (50, 20), 0, 0, 360, (0, 140, 255), -1)   # immobile sous la surface
        if 20 <= t < 32:                            # le « héron » (bleu) au bord du bassin
            cv2.rectangle(im, (40, 300), (140, 520), (255, 60, 30), -1)
        e.write(im)
    e.release()


class FauxDetecteur:
    def detecter(self, image, texte):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        sortie = []
        for bas, haut in (((5, 150, 150), (25, 255, 255)), ((100, 150, 150), (130, 255, 255))):
            masque = cv2.inRange(hsv, np.array(bas), np.array(haut))
            n, _l, stats, _c = cv2.connectedComponentsWithStats(masque)
            sortie.append([(0.8, tuple(int(v) for v in stats[j, :4])) for j in range(1, n) if stats[j, 4] > 300])
        poissons, bleus = sortie
        return poissons, [(s, b, "bird") for s, b in bleus]


class FauxClasseur:
    def intrus(self, image):
        return "Héron", 0.9, True


class FauxDescripteur:
    def decrire(self, image):
        v = np.concatenate([cv2.resize(image, (8, 8)).astype(np.float32).ravel(), [1.0]])
        return (v / np.linalg.norm(v))[None]


d = Path(tempfile.mkdtemp(prefix="fretille_moteur_"))
video = d / "bassin.mp4"
fabriquer(video)
r = reg.Registre(d, FauxDescripteur())
kohaku = r.creer("Kohaku", "Koï")
m.IMMOBILE_SURFACE_S = 20                           # essai : 20 s suffisent
bac = {"id": "b1", "nom": "Bassin", "type": "bassin", "source": str(video), "surface": 0.1, "cm_par_px": 0.5}
evenements, etats, nomme = [], [], []


def sur_image(image, etat):
    etats.append(etat)
    if not nomme:                                   # on nomme le poisson immobile dès qu'il est suivi
        cible = next((p for p in etat["pistes"] if p[1][0] > 700 and p[1][1] < 120), None)
        if cible and s.nommer_piste(cible[0], kohaku):
            nomme.append(cible[0])


s = m.Surveillance(bac, d, FauxDetecteur(), FauxClasseur(), r, analyse_toutes=1.0, sur_image=sur_image,
                   sur_evenement=evenements.append, sur_message=lambda t: None, temps_reel=False)
m.COTE_RECONNAISSANCE = 30                          # poissons de la vidéo d'essai : 100×40 px
s.demarrer()
s.fil.join(180)
verifier("surveillance terminée", not s.fil.is_alive() and s.ok)

nb = [e["nb"] for e in etats[200:]]
verifier("cinq poissons comptés", np.median(nb) == 5, np.median(nb))
mesures = m.lire_mesures(d, "b1", __import__("datetime").date.today())
verifier("mesures minute écrites", mesures and 4 <= mesures[-1]["nb"] <= 5 and mesures[-1]["activite"] > 0, mesures[-1:])
heron = [e for e in evenements if e["genre"] == "predateur"]
verifier("héron signalé une fois, avec photo et clip", len(heron) == 1 and heron[0]["alerte"] and heron[0]["photo"]
         and heron[0]["video"] and (d / heron[0]["photo"]).exists(), heron)
time.sleep(0.2)
clip = d / heron[0]["video"]
lecture = cv2.VideoCapture(str(clip))
verifier("clip du héron lisible", clip.exists() and lecture.get(cv2.CAP_PROP_FRAME_COUNT) > 20, clip)
lecture.release()
immobiles = [e for e in evenements if "immobile" in e["titre"]]
verifier("poisson immobile en surface signalé une fois", len(immobiles) == 1 and immobiles[0]["alerte"]
         and immobiles[0]["genre"] == "sante" and "Kohaku" in immobiles[0]["titre"], [e["titre"] for e in immobiles])
journal = m.lire_evenements(d)
verifier("événements au journal", {e["genre"] for e in journal} == {"predateur", "sante"}, [e["genre"] for e in journal])
verifier("poisson nommé à la main : galerie enrichie", nomme and len(r.fichiers.get(kohaku) or []) >= 1,
         r.fichiers.get(kohaku))
mes = r.mesures(kohaku)
verifier("taille mesurée sur la vidéo (100 px × 0,5 cm)", mes and mes[0]["source"] == "video" and 45 <= mes[0]["cm"] <= 55, mes)
etiquettes = {p[2] for e in etats[-50:] for p in e["pistes"]}
verifier("étiquette affichée sur la vidéo", "Kohaku" in etiquettes, etiquettes)
print(f"\n{ok} vérifications réussies")
