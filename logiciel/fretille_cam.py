"""
Frétille — moteur : surveillance d'un bac (aquarium, bassin, bac d'élevage).

Contrairement à une mangeoire, les poissons sont là en permanence : on ne compte pas des visites,
on mesure en continu l'état du bac, minute par minute :
  - nombre de poissons vus, part en surface (manque d'oxygène) et au fond, activité, vitesse ;
  - poissons immobiles (en surface : sans doute mort) ;
  - au bassin, intrus et prédateurs (héron, chat, martin-pêcheur…) : photo, clip, alerte ;
  - poissons reconnus individuellement (motif unique : koï, discus…) et leur taille, si le bac
    est étalonné (une longueur connue tracée sur l'image).

Un fil lit la caméra (lecteur), un fil analyse la dernière image (détection, suivi, reconnaissance) ;
l'activité, elle, se mesure sur chaque image par simple différence : elle ne coûte presque rien.
"""
import collections
import queue
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

import vision
from i18n import pourcent, t
from outils import LecteurCamera, ajouter_csv, ajuster, ecrire_image, lire_csv
from registre import EXEMPLE_DIFFERENT, MARGE_INDIVIDU, SEUIL_INDIVIDU, SEUIL_PROPOSITION, SUPPLEMENT_FICHE_UNIQUE
from sources import est_fichier, masquer, ouvrir_source, source_depuis_texte

COLONNES_MESURES = ["heure", "nb", "nb_max", "surface", "fond", "activite", "vitesse", "lumiere", "dispersion"]
COLONNES_EVENEMENTS = ["id", "date", "heure", "bac", "genre", "titre", "detail", "photo", "video", "cle"]
FIGEE_S = 60.0                  # direct : sans aucun changement pendant 1 min, l'image est figée
SEUIL_FIGEE = 4
BANDE_SURFACE = 0.08            # sous la ligne d'eau : 8 % de la hauteur de l'image
BANDE_FOND = 0.12
IMMOBILE_SURFACE_S = 10 * 60    # immobile en surface : alerte (poisson sans doute mort ou à l'agonie)
IMMOBILE_S = 60 * 60            # immobile ailleurs : simple note (certains poissons dorment au fond)
INTRUS_SCORE = 0.32
INTRUS_PROBA = 0.40
CLIP_S = 30.0
TAMPON_S = 5.0
IMAGES_SURES = 2                # vues sûres qu'il faut pour nommer un poisson
COTE_RECONNAISSANCE = 60        # en dessous (pixels), un poisson est trop petit pour être reconnu
DESCRIPTIONS_MAX = 4            # vues décrites par analyse : le reste attendra la suivante


def dossier_bac(dossier, bac_id):
    return Path(dossier) / "bacs" / bac_id


def lire_mesures(dossier, bac_id, jour):
    """Lignes minute par minute d'un jour (date) : [{heure, nb, ...}] avec des nombres."""
    lignes = []
    for l in lire_csv(dossier_bac(dossier, bac_id) / "mesures" / f"{jour:%Y-%m-%d}.csv", ("heure",)):
        try:
            lignes.append({"heure": l["heure"], **{c: float(l.get(c) or 0) for c in COLONNES_MESURES[1:]}})
        except ValueError:
            continue
    return lignes


def lire_evenements(dossier):
    return lire_csv(Path(dossier) / "evenements.csv", ("id", "date"))


# ------------------------------------------------------------------
#  Suivi d'un poisson
# ------------------------------------------------------------------
class Piste:
    numero = 0

    def __init__(self, boite, instant):
        Piste.numero += 1
        self.numero = Piste.numero
        self.boite, self.debut, self.vue = boite, instant, instant
        self.trajet = collections.deque(maxlen=400)     # (instant, cx, cy, longueur)
        self.manquee = 0
        self.cle = None             # poisson reconnu (ou nommé à la main)
        self.manuelle = False
        self.votes = collections.defaultdict(list)      # cle -> [(instant, score)]
        self.immobile_note = False
        self.ancre = None           # (instant, cx, cy) : dernier endroit d'où le poisson a vraiment bougé
        self.ajouter(boite, instant)

    @property
    def centre(self):
        x, y, w, h = self.boite
        return x + w / 2, y + h / 2

    @property
    def longueur(self):
        return max(self.boite[2], self.boite[3])

    def ajouter(self, boite, instant):
        self.boite, self.vue, self.manquee = boite, instant, 0
        cx, cy = boite[0] + boite[2] / 2, boite[1] + boite[3] / 2
        lg = max(boite[2], boite[3])
        self.trajet.append((instant, cx, cy, lg))
        if self.ancre is None or np.hypot(cx - self.ancre[1], cy - self.ancre[2]) > max(lg, 8) / 3:
            self.ancre = (instant, cx, cy)
            self.immobile_note = False      # il a bougé : une nouvelle immobilité sera signalée

    def boite_prevue(self, instant, pas):
        """Boîte avancée à la vitesse mesurée entre les deux dernières analyses : entre deux analyses
        (quelques secondes), le cadre suit le poisson au lieu de rester à sa place d'avant."""
        if len(self.trajet) < 2:
            return self.boite
        (t0, x0, y0, _), (t1, x1, y1, _) = self.trajet[-2], self.trajet[-1]
        if not 0 < t1 - t0 <= 3 * pas:
            return self.boite
        dt = min(max(0.0, instant - t1), 1.5 * pas)
        x, y, w, h = self.boite
        return (int(x + (x1 - x0) / (t1 - t0) * dt), int(y + (y1 - y0) / (t1 - t0) * dt), w, h)

    def immobile_depuis(self, instant):
        """Secondes depuis lesquelles le poisson n'a pas bougé de plus d'un tiers de sa longueur."""
        return instant - self.ancre[0] if self.ancre else 0.0

    def vitesse(self):
        """Longueurs de corps par seconde, sur les deux derniers points."""
        if len(self.trajet) < 2:
            return None
        (t0, x0, y0, _), (t1, x1, y1, l1) = self.trajet[-2], self.trajet[-1]
        if t1 - t0 <= 0 or t1 - t0 > 30:
            return None
        return float(np.hypot(x1 - x0, y1 - y0) / max(l1, 1) / (t1 - t0))

    def verdict(self):
        """(cle, score) si au moins IMAGES_SURES vues sûres, espacées, désignent le même poisson."""
        if self.manuelle or self.cle:
            return self.cle, 1.0
        meilleur = None
        for cle, liste in self.votes.items():
            sures = []
            for quand, s in sorted(liste):
                if s >= SEUIL_INDIVIDU and (not sures or quand - sures[-1][0] >= 4):
                    sures.append((quand, s))
            if len(sures) >= IMAGES_SURES and (meilleur is None or len(sures) > meilleur[2]):
                meilleur = (cle, max(s for _t, s in sures), len(sures))
        return (meilleur[0], meilleur[1]) if meilleur else (None, 0.0)

    def probable(self):
        votes = {c: list(v) for c, v in dict(self.votes).items() if v}
        if not votes:
            return None
        cle, liste = max(votes.items(), key=lambda kv: max(s for _t, s in kv[1]))
        return cle if max(s for _t, s in liste) >= SEUIL_PROPOSITION else None


def associer(pistes, boites, instant=None, pas=None):
    """Paires (piste, boîte) par plus proche voisin, normalisé par la taille. Avec instant et pas, chaque
    poisson est cherché là où sa vitesse l'a mené : entre deux analyses espacées de quelques secondes,
    un poisson rapide parcourt plusieurs longueurs. Renvoie (paires, boîtes sans piste)."""
    candidats = []
    for i, p in enumerate(pistes):
        if instant is not None and pas:
            x, y, w, h = p.boite_prevue(instant, pas)
            px, py = x + w / 2, y + h / 2
        else:
            px, py = p.centre
        for j, (_s, b) in enumerate(boites):
            d = np.hypot(b[0] + b[2] / 2 - px, b[1] + b[3] / 2 - py) / max(p.longueur, max(b[2], b[3]), 1)
            taille = max(b[2] * b[3], 1) / max(p.boite[2] * p.boite[3], 1)
            if d <= 2.5 and 0.33 <= taille <= 3:
                candidats.append((d, i, j))
    prises_p, prises_b, paires = set(), set(), []
    for d, i, j in sorted(candidats):
        if i not in prises_p and j not in prises_b:
            prises_p.add(i)
            prises_b.add(j)
            paires.append((pistes[i], boites[j]))
    return paires, [b for j, b in enumerate(boites) if j not in prises_b]


# ------------------------------------------------------------------
#  Surveillance d'un bac
# ------------------------------------------------------------------
class Surveillance:
    """Un bac, une source, un fil. bac : dict de configuration (id, nom, type, source, zone, surface,
    cm_par_px, reconnaissance). Les rappels sont appelés depuis les fils de la surveillance."""
    SILENCE_MAX = 20.0

    def __init__(self, bac, dossier, detecteur=None, classeur=None, registre=None, analyse_toutes=4.0,
                 sur_image=None, sur_evenement=None, sur_message=print, sur_figee=None, sur_fin=None,
                 temps_reel=True):
        self.bac = dict(bac)
        self.id = bac["id"]
        self.source = source_depuis_texte(str(bac.get("source", "0")))
        self.fichier = est_fichier(self.source)
        self.dossier = Path(dossier)
        self.d_bac = dossier_bac(dossier, self.id)
        self.detecteur, self.classeur, self.registre = detecteur, classeur, registre
        self.analyse_toutes = analyse_toutes
        self.sur_image = sur_image or (lambda image, etat: None)
        self.sur_evenement = sur_evenement or (lambda ligne: None)
        self.sur_message = sur_message
        self.sur_figee = sur_figee or (lambda figee: None)
        self.sur_fin = sur_fin or (lambda ok: None)
        self.temps_reel = temps_reel
        self.arret = threading.Event()
        self.pistes = []
        self._verrou = threading.Lock()
        self.a_analyser = queue.Queue(maxsize=1)
        self.activite = 0.0         # part de l'image qui bouge, moyennée (0..1)
        self.lumiere = 0.0
        self.minute = None          # accumulation de la minute en cours
        self.cumul = []
        self.intrus = []            # [(boite, étiquette, danger)] de la dernière analyse
        self.derniere_alerte_intrus = {}
        self.video, self.chemin_clip, self.fin_clip = None, None, 0.0
        self.chemin_prevu = None        # nom du prochain clip, choisi dès la demande (cité par l'événement)
        self.fps_source = 15.0
        self.n_evenement = 0
        self.tampon = collections.deque()
        self.longueurs = collections.defaultdict(list)   # cle -> longueurs (cm) mesurées aujourd'hui
        self.jour_mesures = datetime.now().date()
        self.lecteur = None
        self.image_brute = None
        self.fil = None
        self.ok = True

    # --- contrôle
    def demarrer(self):
        self.fil = threading.Thread(target=self._executer, daemon=True, name=f"bac {self.id}")
        self.fil.start()
        return self

    def arreter(self):
        self.arret.set()

    def nommer_piste(self, numero, cle):
        """Le poisson de la piste `numero` est `cle` (choisi sur la vidéo) ; False s'il est parti."""
        with self._verrou:
            p = next((p for p in self.pistes if p.numero == numero), None)
            if p is None:
                return False
            p.cle, p.manuelle = cle, True
            return True

    def enregistrer(self, secondes=CLIP_S, raison="à la demande"):
        """Clip demandé (ou intrus) : tampon des secondes précédentes, puis `secondes` de plus.
        Renvoie le chemin relatif du clip (en cours ou à venir), pour que l'événement le cite."""
        with self._verrou:
            self.fin_clip = max(self.fin_clip, time.time() + secondes)
            if self.video is not None and self.chemin_clip:
                return f"bacs/{self.id}/clips/{self.chemin_clip.name}"
            if self.chemin_prevu is None:
                self.chemin_prevu = self._nom_clip()
            return f"bacs/{self.id}/clips/{self.chemin_prevu.name}"

    def _nom_clip(self):
        d = self.d_bac / "clips"
        d.mkdir(parents=True, exist_ok=True)
        base = d / (datetime.now() - timedelta(seconds=TAMPON_S)).strftime("%Y-%m-%d_%H-%M-%S")
        chemin, n = base.with_suffix(".mp4"), 1
        while chemin.exists():
            n += 1
            chemin = base.with_name(f"{base.name}-{n}.mp4")
        return chemin

    # --- boucle
    def _executer(self):
        fil_analyse = threading.Thread(target=self._fil_analyse, daemon=True)
        fil_analyse.start()
        camera = None
        try:
            if self.fichier:
                camera, premiere = ouvrir_source(self.source)
                if camera is None:
                    self.sur_message(t("{nom} : aucune image de {source}", nom=self.bac.get("nom", self.id),
                                       source=masquer(self.source)))
                    self.ok = False
                    return
            else:
                self.lecteur = LecteurCamera(self.source, self.sur_message)
                self.lecteur.start()
                self.lecteur.pret.wait(30)
                if self.lecteur.echec or not self.lecteur.pret.is_set():
                    self.sur_message(t("{nom} : aucune image de {source}", nom=self.bac.get("nom", self.id),
                                       source=masquer(self.source)))
                    self.ok = False
                    return
            self._boucle(camera)
        except Exception as e:          # une source bizarre ne doit pas tuer l'application
            self.sur_message(t("{nom} : surveillance arrêtée ({erreur})", nom=self.bac.get("nom", self.id),
                                erreur=f"{type(e).__name__} : {e}"))
            self.ok = False
        finally:
            self.arret.set()
            if camera is not None:
                camera.release()
            if self.lecteur is not None:
                self.lecteur.fin.set()
            fil_analyse.join(30)
            self._fermer_clip()
            self._ecrire_minute()
            self._ecrire_longueurs()
            self.sur_fin(self.ok)

    def _suivante_direct(self):
        try:
            return self.lecteur.file.get(timeout=1)
        except queue.Empty:
            if self.lecteur.en_lecture and time.time() - self.lecteur.derniere > self.SILENCE_MAX:
                self.sur_message(t("{nom} : source figée, reconnexion…", nom=self.bac.get("nom", self.id)))
                self.lecteur.fin.set()
                self.lecteur = LecteurCamera(self.source, self.sur_message, insister=True)
                self.lecteur.start()
            elif not self.lecteur.is_alive() and not self.arret.is_set():
                self.lecteur = LecteurCamera(self.source, self.sur_message, insister=True)
                self.lecteur.start()
            return None

    def _cadrer(self, image):
        z = self.bac.get("zone")
        if not z:
            return image
        h, w = image.shape[:2]
        x0, y0, x1, y1 = int(z[0] * w), int(z[1] * h), int(z[2] * w), int(z[3] * h)
        return image[y0:y1, x0:x1] if x1 - x0 > 32 and y1 - y0 > 32 else image

    def _boucle(self, camera):
        fps_declare = camera.get(cv2.CAP_PROP_FPS) if camera is not None else 0
        fps_declare = fps_declare if 1 < fps_declare < 120 else 15.0
        self.fps_source = fps_declare
        t_debut, nb_lues = time.time(), 0
        prec, t_change, figee = None, time.time(), False
        derniere_analyse = -1e9
        taille = None
        mesure_fps = collections.deque(maxlen=60)
        while not self.arret.is_set():
            if camera is not None:
                ok, image = camera.read()
                if not ok:
                    break
                nb_lues += 1
                instant = nb_lues / fps_declare
                if self.temps_reel:
                    attente = t_debut + instant - time.time()
                    if attente > 0:
                        time.sleep(attente)
            else:
                lu = self._suivante_direct()
                if lu is None:
                    continue
                image, instant = lu
            self.image_brute = image      # image entière : les outils de cadrage travaillent dessus
            image = self._cadrer(image)
            if taille is None:
                taille = (image.shape[1], image.shape[0])
            elif (image.shape[1], image.shape[0]) != taille:
                image = ajuster(image, taille)
            mesure_fps.append(time.time())

            # activité : différence avec l'image précédente, sur une petite version grise
            petite = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(image, (160, max(1, 160 * taille[1] // taille[0]))),
                                                   cv2.COLOR_BGR2GRAY), (5, 5), 0)
            self.lumiere = 0.98 * self.lumiere + 0.02 * float(petite.mean()) if self.lumiere else float(petite.mean())
            if prec is not None:
                diff = cv2.absdiff(petite, prec)
                bouge = float((diff > 10).mean())
                self.activite = 0.9 * self.activite + 0.1 * bouge
                if camera is None:      # image figée : seulement en direct
                    if float(diff.max()) > SEUIL_FIGEE:
                        t_change = time.time()
                        if figee:
                            figee = False
                            self.sur_figee(False)
                    elif not figee and time.time() - t_change > FIGEE_S:
                        figee = True
                        self.sur_figee(True)
            prec = petite

            # tampon d'avant (clips) et clip en cours
            ok_jpeg, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok_jpeg:
                self.tampon.append((time.time(), jpeg))
                while self.tampon and time.time() - self.tampon[0][0] > TAMPON_S:
                    self.tampon.popleft()
            self._clip(image)

            if instant - derniere_analyse >= self.analyse_toutes:
                derniere_analyse = instant
                try:
                    self.a_analyser.put_nowait((image.copy(), instant))
                except queue.Full:
                    pass                # l'analyse précédente n'est pas finie : celle-ci attendra

            fps = (len(mesure_fps) - 1) / (mesure_fps[-1] - mesure_fps[0]) if len(mesure_fps) > 2 and mesure_fps[-1] > mesure_fps[0] else 0
            with self._verrou:
                pistes = [(p.numero, p.boite_prevue(instant, self.analyse_toutes), self._etiquette(p), p.manquee == 0, p.cle)
                          for p in self.pistes if p.manquee == 0]     # une piste perdue n'est plus dessinée
                intrus = list(self.intrus)
            self.sur_image(image, {"pistes": pistes, "nb": sum(1 for p in pistes if p[3]), "activite": self.activite,
                                   "intrus": intrus, "fps": fps, "enregistrement": self.video is not None,
                                   "figee_s": time.time() - t_change if figee else 0, "bac": self.id})

    def _etiquette(self, piste):
        if piste.cle and self.registre is not None:
            f = self.registre.fiches.get(piste.cle)
            if f:
                return f["nom"]
        prob = piste.probable()
        if prob and self.registre is not None and prob in self.registre.fiches:
            return self.registre.fiches[prob]["nom"] + " ?"
        return ""

    # --- clips
    def _clip(self, image):
        maintenant = time.time()
        if self.video is None and maintenant < self.fin_clip:
            self._ouvrir_clip(image)
        if self.video is not None:
            self.video.write(image)
            if maintenant >= self.fin_clip:
                self._fermer_clip()

    def _ouvrir_clip(self, image):
        with self._verrou:
            chemin, self.chemin_prevu = self.chemin_prevu or self._nom_clip(), None
        d = chemin.parent
        # vidéo d'un fichier : son propre débit, sinon le clip passerait au ralenti
        fps = self.lecteur.fps() if self.lecteur is not None else self.fps_source
        video = cv2.VideoWriter(str(chemin), cv2.VideoWriter_fourcc(*"mp4v"), fps, (image.shape[1], image.shape[0]))
        if not video.isOpened():
            self.sur_message(t("Clip impossible à créer dans {dossier}", dossier=d))
            self.fin_clip = 0
            return
        for _t, jpeg in list(self.tampon)[:-1]:
            ancienne = cv2.imdecode(np.frombuffer(jpeg.tobytes() if hasattr(jpeg, "tobytes") else jpeg, np.uint8),
                                    cv2.IMREAD_COLOR)
            if ancienne is not None and ancienne.shape[:2] == image.shape[:2]:
                video.write(ancienne)
        self.video, self.chemin_clip = video, chemin

    def _fermer_clip(self):
        if self.video is not None:
            self.video.release()
            self.video = None
            self.sur_message(t("Clip enregistré : {fichier}", fichier=self.chemin_clip.name))

    # --- analyse
    def _fil_analyse(self):
        while not self.arret.is_set() or not self.a_analyser.empty():
            try:
                image, instant = self.a_analyser.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._analyser(image, instant)
            except Exception as e:      # une analyse ratée ne doit pas arrêter la surveillance
                self.sur_message(t("{nom} : analyse impossible ({erreur})", nom=self.bac.get("nom", self.id),
                                   erreur=f"{type(e).__name__} : {e}"))

    def _analyser(self, image, instant):
        if self.detecteur is None:
            return
        bassin = self.bac.get("type") == "bassin"
        poissons, intrus = self.detecteur.detecter(image, vision.CHERCHE_BASSIN if bassin else vision.CHERCHE_AQUARIUM)
        h, w = image.shape[:2]

        # suivi
        with self._verrou:
            paires, nouvelles = associer(self.pistes, poissons, instant, self.analyse_toutes)
            for p, (_s, b) in paires:
                p.ajouter(b, instant)
            vues = {id(p) for p, _b in paires}
            for p in self.pistes:
                if id(p) not in vues:
                    p.manquee += 1
            for _s, b in nouvelles:
                self.pistes.append(Piste(b, instant))
            perdues = [p for p in self.pistes if p.manquee > 3]
            self.pistes = [p for p in self.pistes if p.manquee <= 3]
            presentes = [p for p in self.pistes if p.manquee == 0]
        for p in perdues:
            self._fin_piste(p)

        # mesures de la minute
        surface = self.bac.get("surface")
        nb = len(presentes)
        en_surface = sum(1 for p in presentes if surface is not None and p.boite[1] < (surface + BANDE_SURFACE) * h)
        au_fond = sum(1 for p in presentes if p.boite[1] + p.boite[3] > (1 - BANDE_FOND) * h)
        vitesses = [v for v in (p.vitesse() for p in presentes) if v is not None]
        if nb >= 2:
            cx = np.array([p.centre[0] for p in presentes]) / w
            cy = np.array([p.centre[1] for p in presentes]) / h
            dispersion = float(np.sqrt(cx.var() + cy.var()))
        else:
            dispersion = 0.0
        self._cumuler({"nb": nb, "surface": en_surface / nb if nb and surface is not None else 0.0,
                       "fond": au_fond / nb if nb else 0.0, "activite": self.activite,
                       "vitesse": float(np.median(vitesses)) if vitesses else 0.0,
                       "lumiere": self.lumiere, "dispersion": dispersion})

        # poissons immobiles
        for p in presentes:
            immobile = p.immobile_depuis(instant)
            haut = surface is not None and p.boite[1] < (surface + BANDE_SURFACE) * h
            seuil = IMMOBILE_SURFACE_S if haut else IMMOBILE_S
            if immobile >= seuil and not p.immobile_note:
                p.immobile_note = True
                nom = self._etiquette(p) or t("Un poisson")
                if haut:
                    titre = t("{nom} immobile en surface", nom=nom)
                elif p.boite[1] + p.boite[3] > (1 - BANDE_FOND) * h:
                    titre = t("{nom} immobile au fond", nom=nom)
                else:
                    titre = t("{nom} immobile", nom=nom)
                minutes = int(immobile // 60)
                detail = (t("Immobile depuis {n} min : à vérifier tout de suite", n=minutes) if haut
                          else t("Immobile depuis {n} min", n=minutes))
                self._evenement("sante" if haut else "info", titre, detail, image, p.boite, alerte=haut)

        # intrus au bassin
        if bassin and self.classeur is not None:
            self._intrus(image, intrus)

        # reconnaissance individuelle et tailles
        if self.registre is not None and self.bac.get("reconnaissance", True):
            self._reconnaitre(image, presentes, instant)
        self._mesurer(presentes)

    def _cumuler(self, mesure):
        minute = datetime.now().replace(second=0, microsecond=0)
        if self.minute is not None and minute != self.minute:
            self._ecrire_minute()
        self.minute = minute
        self.cumul.append(mesure)

    def _ecrire_minute(self):
        if not self.cumul or self.minute is None:
            return
        c = self.cumul
        ligne = {"heure": f"{self.minute:%H:%M}", "nb": f"{np.median([m['nb'] for m in c]):.1f}",
                 "nb_max": f"{max(m['nb'] for m in c)}"}
        for k in ("surface", "fond", "activite", "vitesse", "lumiere", "dispersion"):
            ligne[k] = f"{np.mean([m[k] for m in c]):.4f}"
        self.cumul = []
        try:
            ajouter_csv(self.d_bac / "mesures" / f"{self.minute:%Y-%m-%d}.csv", COLONNES_MESURES, ligne)
        except OSError as e:
            self.sur_message(t("Mesures non écrites : {erreur}", erreur=e))

    def _evenement(self, genre, titre, detail, image=None, boite=None, cle="", alerte=False, video=None):
        """Note un événement (photo à l'appui) ; alerte=True le signale au téléphone (via l'application)."""
        maintenant = datetime.now()
        with self._verrou:
            self.n_evenement += 1
            n = self.n_evenement
        ident = f"{maintenant:%Y-%m-%d_%H-%M-%S}_{maintenant.microsecond // 1000:03d}_{self.id}_{n}"
        photo = ""
        if image is not None:
            vue = image.copy()
            if boite is not None:
                x, y, bw, bh = (int(v) for v in boite)
                cv2.rectangle(vue, (x, y), (x + bw, y + bh), (65, 164, 217), 3)
            d = self.d_bac / "photos"
            d.mkdir(parents=True, exist_ok=True)
            if ecrire_image(d / f"{ident}.jpg", vue, 88):
                photo = f"bacs/{self.id}/photos/{ident}.jpg"
        ligne = {"id": ident, "date": f"{maintenant:%Y-%m-%d}", "heure": f"{maintenant:%H:%M:%S}", "bac": self.id,
                 "genre": genre, "titre": titre, "detail": detail, "photo": photo,
                 "video": video if video is not None else
                 (f"bacs/{self.id}/clips/{self.chemin_clip.name}" if self.video is not None and self.chemin_clip else ""),
                 "cle": cle}
        try:
            ajouter_csv(self.dossier / "evenements.csv", COLONNES_EVENEMENTS, ligne)
        except OSError as e:
            self.sur_message(t("Événement non écrit : {erreur}", erreur=e))
        self.sur_evenement({**ligne, "alerte": alerte, "nom_bac": self.bac.get("nom", self.id)})

    def _intrus(self, image, intrus):
        vus = []
        for score, boite, _texte in intrus:
            if score < INTRUS_SCORE:
                continue
            vue = vision.recadrer(image, boite, 0.15)
            if vue.size == 0:
                continue
            etiquette, proba, danger = self.classeur.intrus(vue)
            if proba < INTRUS_PROBA:
                continue
            vus.append((boite, etiquette, danger))
            if danger and time.time() - self.derniere_alerte_intrus.get(etiquette, 0) > 600:
                self.derniere_alerte_intrus[etiquette] = time.time()
                video = self.enregistrer(CLIP_S, etiquette)
                self._evenement("predateur", t("{intrus} au bassin", intrus=t(etiquette)),
                                t("Repéré au bord ou dans l'eau ({proba})", proba=pourcent(proba)),
                                image, boite, "", True, video=video)
        with self._verrou:
            self.intrus = vus

    def _reconnaitre(self, image, presentes, instant):
        connus = any(v is not None for v in list(self.registre.vecteurs.values()))
        if not connus and not any(p.manuelle for p in presentes):
            return
        decrits = 0
        for p in sorted(presentes, key=lambda p: -p.longueur):
            if decrits >= DESCRIPTIONS_MAX:
                break
            if min(p.boite[2:]) < COTE_RECONNAISSANCE:
                continue
            seul = all(vision.iou(p.boite, q.boite) < 0.05 for q in presentes if q is not p)
            if not seul:
                continue
            poisson = vision.isoler(image, p.boite)
            if poisson is None:
                continue
            decrits += 1
            vecteurs = np.vstack([self.registre.descripteur.decrire(poisson),
                                  self.registre.descripteur.decrire(cv2.flip(poisson, 1))])
            if p.manuelle:              # nommé à la main : ses vues isolées enrichissent sa galerie
                g = self.registre.vecteurs.get(p.cle)
                if p.cle in self.registre.fiches and (g is None or float((vecteurs[:1] @ g.T).max()) < EXEMPLE_DIFFERENT):
                    self.registre.ajouter_exemple(p.cle, poisson)
                continue
            sift = vision.points_sift(poisson)
            sift_miroir = vision.points_sift(cv2.flip(poisson, 1))
            sims = self.registre.similarites(vecteurs, (*sift, sift_miroir))
            if not sims:
                continue
            cle, score = sims[0]
            deuxieme = sims[1][1] if len(sims) > 1 else -1.0
            seuil = SEUIL_INDIVIDU + (SUPPLEMENT_FICHE_UNIQUE if len(sims) == 1 else 0.0)
            if score >= seuil and score - deuxieme >= MARGE_INDIVIDU:
                p.votes[cle].append((instant, score))
            elif score >= SEUIL_PROPOSITION:
                p.votes[cle].append((instant, min(score, SEUIL_INDIVIDU - 0.01)))
            trouve, _s = p.verdict()
            if trouve and not p.cle:
                p.cle = trouve
            if p.cle:                   # vue nouvelle d'un poisson reconnu : elle enrichit sa galerie
                g = self.registre.vecteurs.get(p.cle)
                if g is None or float((vecteurs[:1] @ g.T).max()) < EXEMPLE_DIFFERENT:
                    self.registre.ajouter_exemple(p.cle, poisson)

    def _mesurer(self, presentes):
        """Longueur des poissons reconnus, si le bac est étalonné (cm par pixel)."""
        cm_px = self.bac.get("cm_par_px")
        if datetime.now().date() != self.jour_mesures:
            self._ecrire_longueurs()
            self.jour_mesures = datetime.now().date()
        if not cm_px:
            return
        for p in presentes:
            if p.cle and min(p.boite[2:]) >= 20:
                self.longueurs[p.cle].append(p.longueur * cm_px)

    def _ecrire_longueurs(self):
        """Une mesure « vidéo » par poisson et par jour : le 80e centile des longueurs vues (un
        poisson de biais paraît plus court, jamais plus long)."""
        if self.registre is None:
            self.longueurs.clear()
            return
        jour = self.jour_mesures.isoformat()
        for cle, valeurs in list(self.longueurs.items()):
            if len(valeurs) < 8 or cle not in self.registre.fiches:
                continue
            avant = next((m for m in self.registre.mesures(cle) if m["date"] == jour and m.get("source") == "video"), None)
            try:
                deja = int(str(avant.get("note", "")).removeprefix("vues=")) if avant else 0
            except ValueError:
                deja = 0
            if deja >= len(valeurs):        # une session plus longue a déjà mesuré ce jour-là
                continue
            try:
                self.registre.ajouter_mesure(cle, jour, cm=float(np.percentile(valeurs, 80)), source="video",
                                             note=f"vues={len(valeurs)}")
            except (ValueError, OSError):
                pass
        self.longueurs.clear()

    def _fin_piste(self, p):
        """Un poisson reconnu ajoute sa vue d'ensemble à l'album ? Non : rien à écrire, le suivi
        d'un poisson n'a pas de « visite ». Seule une piste nommée à la main enrichit la galerie."""
        return None


def etalonner(x0, y0, x1, y1, centimetres):
    """cm par pixel à partir d'un trait tracé sur l'image et de sa longueur réelle."""
    pixels = float(np.hypot(x1 - x0, y1 - y0))
    if pixels < 10 or not 0.5 <= centimetres <= 1000:
        raise ValueError(t("trait trop court ou longueur impossible"))
    return centimetres / pixels
