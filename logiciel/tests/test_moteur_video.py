"""Aide aux essais : vidéo de bassin fabriquée et faux modèles (couleurs). Repris de test_moteur.py."""
import cv2
import numpy as np


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
