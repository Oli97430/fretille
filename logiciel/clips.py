"""
Frétille — clips verticaux (9:16, 1080×1920) pour Shorts, Reels et TikTok.

À partir d'un clip du bac : un cadre vertical qui suit le poisson (ou l'intrus) (positions enregistrées
pendant la visite, lissées pour ne pas trembler), 25 s au plus, habillé du nom et de l'âge.
Encodé en H.264 par ffmpeg s'il est installé (accepté partout), sinon en MPEG-4 par OpenCV.
"""
import os
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from i18n import police_pil, t

LARGEUR, HAUTEUR = 1080, 1920
DUREE_MAX = 25.0
DUREE_MIN = 6.0
AVANT = 3.0                 # par défaut, le clip commence ~3 s avant l'arrivée (tampon du moteur)
GARDER = 60                 # clips verticaux gardés dans partage/clips
ZOOM = 0.7                  # le cadre prend 70 % de la hauteur : de la marge pour suivre aussi en hauteur


def _police(taille, gras=False):
    return police_pil(taille, gras)


def _aujourd_hui():
    n = datetime.now()
    return t("{jour}/{mois}/{annee}", jour=f"{n:%d}", mois=f"{n:%m}", annee=f"{n:%Y}")


def trouver_ffmpeg():
    trouve = shutil.which("ffmpeg")
    if trouve:
        return trouve
    for d in (Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin",
              Path.home() / "scoop" / "apps" / "ffmpeg-shared" / "current" / "bin",
              Path("C:/ffmpeg/bin"), Path("C:/Program Files/ffmpeg/bin")):
        if (d / "ffmpeg.exe").exists():
            return str(d / "ffmpeg.exe")
    return None


def decalage(ligne):
    """Secondes entre le début du fichier vidéo et l'événement. Plusieurs événements
    peuvent partager un fichier : le nom du fichier donne son début."""
    try:
        debut = datetime.strptime(Path(ligne["video"]).stem[:19], "%Y-%m-%d_%H-%M-%S")
        arrivee = datetime.strptime(f"{ligne['date']} {ligne['heure'][:8]}", "%Y-%m-%d %H:%M:%S")
        return max(0.0, (arrivee - debut).total_seconds())
    except (KeyError, ValueError):
        return AVANT


def centres(positions, fps, nb_images, largeur_source, axe=0, decale=AVANT):
    """Centre du cadre pour chaque image (axe 0 : horizontal, 1 : vertical) : positions
    interpolées puis lissées. positions : [(t_s depuis l'arrivée, x, y, l, h, image_l, image_h)] ;
    sans positions, le milieu. largeur_source : taille de la source sur cet axe."""
    if not positions:
        return np.full(nb_images, largeur_source / 2)
    echelle = largeur_source / positions[0][5 + axe]    # positions mesurées sur l'image analysée
    temps = np.array([p[0] + decale for p in positions])
    x = np.array([(p[1 + axe] + p[3 + axe] / 2) * echelle for p in positions])
    ordre = np.argsort(temps)
    brut = np.interp(np.arange(nb_images) / fps, temps[ordre], x[ordre])
    # lissage aller-retour : suit le poisson sans à-coups ni retard
    a = min(1.0, 1.5 / max(fps, 1))
    lisse = brut.copy()
    for i in range(1, len(lisse)):
        lisse[i] = lisse[i - 1] + a * (brut[i] - lisse[i - 1])
    for i in range(len(lisse) - 2, -1, -1):
        lisse[i] = lisse[i + 1] + a * (lisse[i] - lisse[i + 1])
    return lisse


def habillage(nom, sous_titre, quand, en_haut=False, legende=None):
    """Calque RGBA 1080×1920 : marque en haut, nom et âge en bas, sur dégradés lisibles.
    en_haut : nom et âge sous la marque, quand le poisson est dans le bas de l'image.
    legende : None pour « au bassin » dans la langue de l'interface."""
    if legende is None:
        legende = t("au bassin")
    calque = Image.new("RGBA", (LARGEUR, HAUTEUR), (0, 0, 0, 0))
    d = ImageDraw.Draw(calque)
    haut = 560 if en_haut else 260
    for i in range(haut):                                  # dégradé du haut
        d.line([(0, i), (LARGEUR, i)], fill=(0, 0, 0, int(160 * (1 - i / haut))))
    if not en_haut:
        for i in range(520):                               # dégradé du bas
            d.line([(0, HAUTEUR - 1 - i), (LARGEUR, HAUTEUR - 1 - i)], fill=(0, 0, 0, int(160 * (1 - i / 520))))
    f_marque, f_nom, f_sous, f_petit = _police(52, True), _police(110, True), _police(48), _police(40)
    d.rounded_rectangle([60, 90, 120 + d.textlength("Frétille", font=f_marque), 180], radius=30, fill=(16, 20, 15, 200))
    d.text((90, 135), "Frétille", font=f_marque, fill=(233, 238, 227), anchor="lm")
    d.text((LARGEUR - 70, 135), quand, font=f_petit, fill=(233, 238, 227, 220), anchor="rm")
    while d.textlength(nom, font=f_nom) > LARGEUR - 140 and f_nom.size > 50:
        f_nom = _police(f_nom.size - 8, True)
    y = 330 if en_haut else HAUTEUR - 330
    d.text((70, y), nom, font=f_nom, fill=(255, 255, 255), anchor="ls")
    d.text((74, y + 80), sous_titre, font=f_sous, fill=(217, 164, 65), anchor="ls")
    d.text((74, y + 145), legende, font=f_petit, fill=(233, 238, 227, 200), anchor="ls")
    return calque


def faire_clip(source, sortie, nom, sous_titre="", positions=(), quand=None, ffmpeg="auto", decale=AVANT, legende=None):
    """Écrit le clip vertical ; renvoie le chemin, ou None si la vidéo est trop courte ou illisible."""
    source, sortie = Path(source), Path(sortie)
    lecture = cv2.VideoCapture(str(source))
    if not lecture.isOpened():
        return None
    fps = lecture.get(cv2.CAP_PROP_FPS) or 15
    fps = fps if 1 <= fps <= 120 else 15
    total = int(lecture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sl, sh = int(lecture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(lecture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    debut = int(max(0.0, decale - 1.0) * fps)          # une seconde avant l'arrivée
    # écran d'attente du lecteur (« wait… », image grise ou noire) au début : on le saute, 15 s au plus
    lecture.set(cv2.CAP_PROP_POS_FRAMES, debut)
    for _ in range(int(15 * fps)):
        ok, image = lecture.read()
        if not ok or float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).std()) >= 10:     # jardin ~40, écran d'attente ~5
            break
        debut += 1
    nb = min(total - debut, int(DUREE_MAX * fps))
    if nb < DUREE_MIN * fps or sl < 16 or sh < 16:
        lecture.release()
        return None
    lecture.set(cv2.CAP_PROP_POS_FRAMES, debut)
    # cadre 9:16 dans la source, un peu zoomé pour suivre le poisson en largeur et en hauteur
    ch = int(sh * ZOOM)
    cl = int(ch * LARGEUR / HAUTEUR)
    if cl > sl:                         # source étroite : toute la largeur
        cl, ch = sl, min(sh, int(sl * HAUTEUR / LARGEUR))
    xs = centres(list(positions), fps, total, sl, 0, decale)[debut:debut + nb]
    ys = centres(list(positions), fps, total, sh, 1, decale)[debut:debut + nb]
    # sujet surtout dans le bas du cadre (bord de l'image) : le nom passe en haut pour ne pas la cacher
    y0s = np.clip(ys - ch / 2, 0, sh - ch)
    en_haut = bool(len(ys)) and float(np.median((ys - y0s) / ch)) > 0.55
    calque = habillage(nom, sous_titre, quand or _aujourd_hui(), en_haut, legende)
    alpha = np.asarray(calque)[..., 3:4].astype(np.float32) / 255
    couleur = np.asarray(calque)[..., 2::-1].astype(np.float32)          # RGBA -> BGR
    zones = [slice(0, 600)] if en_haut else [slice(0, 260), slice(HAUTEUR - 540, HAUTEUR)]   # parties habillées

    sortie.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = trouver_ffmpeg() if ffmpeg == "auto" else ffmpeg
    tmp = Path(tempfile.mkdtemp(prefix="fretille_clip_")) / "brut.mp4"
    # tout s'écrit à part : la page du téléphone ne voit jamais un clip à moitié fait,
    # et un essai raté n'écrase pas un clip réussi du même nom
    ecriture = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (LARGEUR, HAUTEUR))
    ecrites = 0
    try:
        for i in range(nb):
            ok, image = lecture.read()
            if not ok:
                break
            if float(cv2.cvtColor(image[::8, ::8], cv2.COLOR_BGR2GRAY).std()) < 10:
                continue                # écran d'attente du lecteur en cours de route : coupé
            x0 = int(np.clip(xs[min(i, len(xs) - 1)] - cl / 2, 0, sl - cl))
            y0 = int(np.clip(ys[min(i, len(ys) - 1)] - ch / 2, 0, sh - ch))
            cadre = cv2.resize(image[y0:y0 + ch, x0:x0 + cl], (LARGEUR, HAUTEUR), interpolation=cv2.INTER_CUBIC)
            for z in zones:
                bande = cadre[z].astype(np.float32)
                cadre[z] = (bande * (1 - alpha[z]) + couleur[z] * alpha[z]).astype(np.uint8)
            ecriture.write(cadre)
            ecrites += 1
    finally:
        lecture.release()
        ecriture.release()
    if ecrites < DUREE_MIN * fps:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        return None
    final = tmp
    if ffmpeg:
        # H.264 + yuv420p + faststart : le format que YouTube, Instagram et TikTok acceptent tous
        h264 = tmp.with_name("h264.mp4")
        try:
            r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(tmp), "-c:v", "libx264", "-preset", "medium",
                                "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(h264)],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=600)
            if r.returncode == 0 and h264.exists():
                final = h264
        except (OSError, subprocess.TimeoutExpired):
            pass                        # ffmpeg en échec : on garde la version OpenCV
    partiel = sortie.with_name(sortie.name + ".part")
    shutil.move(str(final), str(partiel))
    os.replace(partiel, sortie)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return sortie


def menage(dossier, garder=GARDER):
    """Garde les clips les plus récents."""
    clips = sorted(Path(dossier).glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in clips[garder:]:
        p.unlink(missing_ok=True)


def nom_clip(ligne, nom):
    sans = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()     # « Zoé » → « Zoe »
    propre = "".join(c if c.isalnum() else "_" for c in sans).strip("_")[:30] or "poisson"
    return f"{ligne['date']}_{ligne['heure'][:5].replace(':', '-')}_{propre}.mp4"
