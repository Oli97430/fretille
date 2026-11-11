"""
Frétille — registre des poissons reconnus individuellement (koï, discus, bettas…).

poissons/registre.json : une fiche par poisson (nom, espèce, bac, sexe, naissance, origine,
signes distinctifs, notes, mesures, carnet) ; poissons/<id>/*.jpg : galerie de vues apprises,
lue par la reconnaissance ; poissons/<id>/album/ : photos souvenirs, jamais lues par elle.
Repris du registre de Broutille.
"""
import json
import shutil
import threading
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

import vision
from i18n import nombre, t
from outils import date_photo, ecrire_image, insister, lire_image, nom_fichier

MAX_EXEMPLES = 60               # vues gardées par poisson
EXEMPLE_DIFFERENT = 0.92        # une nouvelle vue n'entre en galerie que si elle apporte du neuf
ALBUM = "album"
CHAMPS_IDENTITE = ("sexe", "naissance", "arrivee", "origine", "signes", "notes", "profil", "bac", "lot")
Image.MAX_IMAGE_PIXELS = 60_000_000     # une « image » géante (bombe de décompression) est refusée

BONUS_SIFT = 0.01               # score ajouté par point de motif cohérent
PLAFOND_SIFT = 40
SEUIL_INDIVIDU = 0.70           # score minimal d'une image pour compter comme une reconnaissance sûre
MARGE_INDIVIDU = 0.03           # écart minimal avec le deuxième poisson le plus proche
SEUIL_PROPOSITION = 0.40        # en dessous, un poisson ne ressemble pas assez pour être proposé
SUPPLEMENT_FICHE_UNIQUE = 0.15  # une seule fiche : sans deuxième candidat, la marge ne protège de rien


class RegistreIllisible(Exception):
    """registre.json et sa sauvegarde sont abîmés : mieux vaut s'arrêter que repartir de zéro."""


class Registre:
    """Poissons connus : poissons/registre.json + une galerie de vues par poisson
    (poissons/<id>/*.jpg). Les vecteurs sont recalculés seulement pour les nouvelles photos."""

    def __init__(self, dossier, descripteur=None):
        self.dossier = Path(dossier) / "poissons"
        self.descripteur = descripteur or vision.Descripteur()
        self.fiches = {}
        self.vecteurs = {}      # id -> (n, d) : un vecteur par photo, dans le sens de la photo
        self.fichiers = {}      # id -> [noms des photos], dans l'ordre des vecteurs
        self.points = {}        # chemin de photo -> points SIFT
        self.rejets = None      # vecteurs des « Pas un poisson »
        self.alias = {}         # fiche fusionnée -> fiche qui l'a absorbée, pour les visites en cours
        self._verrou = threading.RLock()
        self.charger()

    @property
    def chemin(self):
        return self.dossier / "registre.json"

    def charger(self):
        with self._verrou:
            if not self.chemin.exists() and not self.chemin.with_suffix(".bak").exists():
                self.fiches = {}
                return
            for chemin in (self.chemin, self.chemin.with_suffix(".bak")):
                try:
                    self.fiches = json.loads(chemin.read_text(encoding="utf-8"))
                    return
                except (OSError, ValueError):
                    pass
            raise RegistreIllisible(t("Fiches des poissons illisibles : {chemin}", chemin=self.chemin))

    def _ecrire(self):
        self.dossier.mkdir(parents=True, exist_ok=True)
        if self.chemin.exists():
            try:
                json.loads(self.chemin.read_text(encoding="utf-8"))
                shutil.copy2(self.chemin, self.chemin.with_suffix(".bak"))
            except (OSError, ValueError):
                pass                    # fichier principal abîmé : la copie .bak reste la bonne
        tmp = self.chemin.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.fiches, indent=1, ensure_ascii=False), encoding="utf-8")
        insister(lambda: tmp.replace(self.chemin))     # antivirus, OneDrive : verrou passager

    def copie_fiches(self):
        """Fiches à parcourir depuis un autre fil."""
        with self._verrou:
            return {c: dict(f) for c, f in self.fiches.items()}

    def resoudre(self, cle):
        """Fiche actuelle d'un identifiant, en suivant les fusions ; None si elle n'existe plus."""
        with self._verrou:
            while cle in self.alias:
                cle = self.alias[cle]
            return cle if cle in self.fiches else None

    def preparer(self):
        """Lit les galeries ; à appeler une fois les modèles disponibles (peut prendre du temps)."""
        with self._verrou:
            for cle in list(self.fiches):
                self._lire_galerie(cle)
            self._lire_rejets()

    def _vecteurs_dossier(self, d):
        cache = d / "vecteurs.npz"
        try:
            anciens = dict(np.load(cache)) if cache.exists() else {}
        except (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile):
            anciens = {}        # cache abîmé : il se recalcule depuis les photos
        gardes = {}
        for p in sorted(d.glob("*.jpg")):
            k = f"{p.name}|{p.stat().st_size}"
            v = anciens.get(k)
            if v is None:
                image = lire_image(p)
                if image is None:
                    continue
                v = self.descripteur.decrire(image)[0]
            gardes[k] = v
        if set(gardes) != set(anciens) and d.exists():
            tmp = d / "vecteurs.tmp.npz"
            np.savez(tmp, **gardes)
            tmp.replace(cache)
        noms = [k.split("|")[0] for k in gardes]
        return noms, (np.stack(list(gardes.values())) if gardes else None)

    def _lire_galerie(self, cle):
        with self._verrou:
            noms, v = self._vecteurs_dossier(self.dossier / cle)
            self.fichiers[cle], self.vecteurs[cle] = noms, v

    def _lire_rejets(self):
        d = self.dossier / "pas_un_poisson"
        self.rejets = self._vecteurs_dossier(d)[1] if d.exists() else None

    # --- gestion
    def creer(self, nom, espece, provisoire=False):
        with self._verrou:
            cle = f"t{int(time.time() * 1000):x}"
            while cle in self.fiches:
                cle += "x"
            self.fiches[cle] = {"nom": nom, "espece": espece, "provisoire": provisoire,
                                "creee": datetime.now().strftime("%Y-%m-%d")}
            (self.dossier / cle).mkdir(parents=True, exist_ok=True)
            self._ecrire()
            self.fichiers[cle], self.vecteurs[cle] = [], None
            return cle

    def nom_provisoire(self, espece):
        court = espece.split(" (")[0]
        with self._verrou:
            noms = [f["nom"] for f in self.fiches.values()]
        n = 1 + sum(1 for nom in noms if nom.startswith(f"{court} n°"))
        while f"{court} n°{n}" in noms:
            n += 1
        return f"{court} n°{n}"

    def modifier(self, cle, **champs):
        with self._verrou:
            self.fiches[cle].update(champs)
            self._ecrire()

    def ajouter_exemple(self, cle, poisson, nom=None):
        """Ajoute un poisson isolé à la galerie ; renvoie le nom du fichier. Galerie pleine : la plus
        ancienne vue automatique laisse sa place, car un poisson grandit et change de couleurs ; les photos importées restent."""
        with self._verrou:
            if cle not in self.fiches:
                return None
            d = self.dossier / cle
            d.mkdir(parents=True, exist_ok=True)
            photos = sorted(d.glob("*.jpg"))
            if len(photos) >= MAX_EXEMPLES:
                anciennes = [p for p in photos if not p.name.startswith("import_")]
                if not anciennes:
                    return None
                anciennes[0].unlink(missing_ok=True)
                self.points.pop(str(anciennes[0]), None)
            nom = nom or f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{datetime.now().microsecond:06d}.jpg"
            ecrire_image(d / nom, poisson)
            self._lire_galerie(cle)
            return nom

    def retirer_exemples(self, cle, prefixe):
        """Retire les photos d'une visite : [(nom, contenu)]."""
        with self._verrou:
            retires = list((self.dossier / cle).glob(prefixe + "_*.jpg"))
            contenus = [(p.name, p.read_bytes()) for p in retires]
            for p in retires:
                p.unlink(missing_ok=True)
                self.points.pop(str(p), None)
            if retires:
                self._lire_galerie(cle)
            return contenus

    def retirer_vue(self, cle, nom):
        """Retire une vue de la galerie (mauvaise image apprise) ; la reconnaissance l'oublie aussitôt."""
        with self._verrou:
            chemin = self.dossier / cle / Path(nom).name
            chemin.unlink(missing_ok=True)
            self.points.pop(str(chemin), None)
            if self.fiches.get(cle, {}).get("profil") == chemin.name:
                self.fiches[cle].pop("profil")
                self._ecrire()
            if cle in self.fiches:
                self._lire_galerie(cle)

    def deposer_exemples(self, cle, contenus):
        with self._verrou:
            d = self.dossier / cle
            d.mkdir(parents=True, exist_ok=True)
            for nom, octets in contenus:
                (d / nom).write_bytes(octets)
            self._lire_galerie(cle)

    def rejeter(self, contenus):
        """Ces images ne sont pas des poissons : la prochaine fois, ne pas les compter."""
        d = self.dossier / "pas_un_poisson"
        d.mkdir(parents=True, exist_ok=True)
        with self._verrou:
            for nom, octets in contenus:
                (d / nom).write_bytes(octets)
            self._lire_rejets()

    def nombre_rejets(self):
        d = self.dossier / "pas_un_poisson"
        return len(list(d.glob("*.jpg"))) if d.exists() else 0

    def oublier_rejets(self):
        """Annule tous les « Pas un poisson »."""
        with self._verrou:
            shutil.rmtree(self.dossier / "pas_un_poisson", ignore_errors=True)
            self.rejets = None

    def fusionner(self, source, cible):
        """Le poisson `source` était en fait `cible` : galerie réunie, `source` disparaît."""
        with self._verrou:
            ds, dc = self.dossier / source, self.dossier / cible
            dc.mkdir(parents=True, exist_ok=True)
            for p in ds.glob("*.jpg"):
                p.replace(dc / p.name)
            if (ds / ALBUM).is_dir():           # l'album suit : rmtree l'effacerait
                (dc / ALBUM).mkdir(exist_ok=True)
                for p in (ds / ALBUM).iterdir():
                    if p.is_file():
                        dest, n = dc / ALBUM / p.name, 1
                        while dest.exists():        # même nom : renommer, jamais perdre une photo
                            n += 1
                            dest = dc / ALBUM / f"{p.stem}_f{n}{p.suffix}"
                        p.replace(dest)
            shutil.rmtree(ds, ignore_errors=True)
            fs, fc = self.fiches[source], self.fiches[cible]
            fc["provisoire"] = False if not fs["provisoire"] else fc["provisoire"]
            for liste in ("mesures", "carnet"):
                tout = fc.get(liste, []) + fs.get(liste, [])
                if tout:
                    fc[liste] = sorted(tout, key=lambda p: p["date"])
            for champ in CHAMPS_IDENTITE:       # la fiche gardée complète ce qui lui manque
                if fs.get(champ) and not fc.get(champ):
                    fc[champ] = fs[champ]
            del self.fiches[source]
            self.alias[source] = cible
            self.vecteurs.pop(source, None)
            self.fichiers.pop(source, None)
            self._ecrire()
            self._lire_galerie(cible)

    # --- mesures : [{"date": "AAAA-MM-JJ", "cm": 12.5, "g": 0 ou poids, "source": "video"|"main", "note"}]
    def ajouter_mesure(self, cle, jour, cm=None, grammes=None, source="main", note=""):
        """Ajoute une mesure ; une seconde mesure du même jour et de la même source remplace la première.
        Une mesure de la vidéo n'écrase jamais une mesure faite à la main."""
        cm = float(cm) if cm not in (None, "") else None
        grammes = float(grammes) if grammes not in (None, "") else None
        if cm is None and grammes is None:
            raise ValueError(t("ni longueur ni poids"))
        if cm is not None and not 0.3 <= cm <= 150:
            raise ValueError(t("longueur hors limites ({mini} à {maxi} cm)", mini=nombre(0.3), maxi=150))
        if grammes is not None and not 0.05 <= grammes <= 50000:
            raise ValueError(t("poids hors limites ({mini} g à {maxi} kg)", mini=nombre(0.05, 2), maxi=50))
        jour = date.fromisoformat(str(jour)).isoformat()
        with self._verrou:
            mesures = [m for m in self.fiches[cle].get("mesures", []) if not (m["date"] == jour and m.get("source") == source)]
            m = {"date": jour, "source": source, "note": note.strip()}
            if cm is not None:
                m["cm"] = round(cm, 1)
            if grammes is not None:
                m["g"] = round(grammes, 1)
            mesures.append(m)
            self.fiches[cle]["mesures"] = sorted(mesures, key=lambda x: (x["date"], x.get("source", "")))
            self._ecrire()

    def retirer_mesure(self, cle, jour, source):
        with self._verrou:
            self.fiches[cle]["mesures"] = [m for m in self.fiches[cle].get("mesures", [])
                                           if not (m["date"] == jour and m.get("source") == source)]
            self._ecrire()

    def mesures(self, cle):
        with self._verrou:
            return [dict(m) for m in self.fiches.get(cle, {}).get("mesures", [])]

    # --- carnet : [{"date", "type", "texte"}] par ordre de date (soins, vétérinaire, ponte…)
    def ajouter_note(self, cle, jour, genre, texte):
        texte = texte.strip()
        if not texte:
            raise ValueError(t("note vide"))
        jour = date.fromisoformat(str(jour)).isoformat()
        with self._verrou:
            carnet = self.fiches[cle].get("carnet", []) + [{"date": jour, "type": genre, "texte": texte}]
            self.fiches[cle]["carnet"] = sorted(carnet, key=lambda n: n["date"])
            self._ecrire()

    def retirer_note(self, cle, rang, attendue=None):
        """Retire la note n° rang. attendue : la note affichée ; si une note ajoutée entre-temps (depuis
        le téléphone) a décalé les rangs, c'est elle qu'on cherche, et rien n'est retiré si elle a disparu."""
        with self._verrou:
            carnet = list(self.fiches[cle].get("carnet", []))
            if attendue is not None and not (0 <= rang < len(carnet) and carnet[rang] == attendue):
                rang = next((i for i, n in enumerate(carnet) if n == attendue), -1)
            if 0 <= rang < len(carnet):
                del carnet[rang]
                self.fiches[cle]["carnet"] = carnet
                self._ecrire()

    def carnet(self, cle):
        with self._verrou:
            return [dict(n) for n in self.fiches.get(cle, {}).get("carnet", [])]

    # --- album : photos souvenirs (poissons/<id>/album/), hors reconnaissance
    def album(self, cle):
        d = self.dossier / cle / ALBUM
        return sorted((p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")),
                      reverse=True) if d.is_dir() else []

    def ajouter_album(self, cle, chemins):
        """Copie les photos dans l'album, réduites à 2000 px ; renvoie les noms écrits.
        Le nom commence par la date de prise de vue (EXIF) ou du fichier : l'album se trie seul."""
        d = self.dossier / cle / ALBUM
        d.mkdir(parents=True, exist_ok=True)
        ecrits = []
        for chemin in chemins:
            chemin = Path(chemin)
            try:
                with Image.open(chemin, formats=["JPEG", "PNG", "WEBP", "HEIF", "MPO"]) as img:
                    quand = date_photo(img, chemin)
                    img.draft("RGB", (2000, 2000))
                    img = ImageOps.exif_transpose(img).convert("RGB")
                    img.thumbnail((2000, 2000))
                    base = f"{quand:%Y-%m-%d_%H-%M-%S}_{nom_fichier(chemin.stem)[:30]}"
                    cible, n = d / f"{base}.jpg", 1
                    while cible.exists():
                        n += 1
                        cible = d / f"{base}_{n}.jpg"
                    partiel = cible.with_name(cible.name + ".part")
                    img.save(partiel, "JPEG", quality=90)
                    partiel.replace(cible)          # jamais de photo à moitié écrite dans l'album
                    ecrits.append(cible.name)
            except Exception:           # image énorme (bombe de décompression), format exotique… : ignorée
                continue
        return ecrits

    def retirer_album(self, cle, nom):
        with self._verrou:
            (self.dossier / cle / ALBUM / nom).unlink(missing_ok=True)
            if self.fiches.get(cle, {}).get("profil") == f"{ALBUM}/{nom}":
                self.fiches[cle].pop("profil")
                self._ecrire()

    def photo_profil(self, cle):
        """Photo choisie, sinon la plus récente de l'album, sinon la première vue de la galerie."""
        d = self.dossier / cle
        choisie = self.fiches.get(cle, {}).get("profil")
        if choisie and (d / choisie).is_file():
            return d / choisie
        album = self.album(cle)
        if album:
            return album[0]
        vues = sorted(d.glob("*.jpg")) if d.is_dir() else []
        return vues[0] if vues else None

    def supprimer(self, cle):
        with self._verrou:
            shutil.rmtree(self.dossier / cle, ignore_errors=True)
            self.fiches.pop(cle, None)
            self.vecteurs.pop(cle, None)
            self.fichiers.pop(cle, None)
            self._ecrire()

    # --- reconnaissance
    def est_rejet(self, vecteurs, seuil=0.85):
        return self.rejets is not None and float((vecteurs @ self.rejets.T).max()) >= seuil

    def similarites(self, vecteurs, sift=None, candidats=3):
        """[(id, score)] du plus au moins probable. Score : photo de la galerie la plus ressemblante
        (cosinus) : un poisson ne ressemble qu'à ses vues prises du même côté. Le motif de la robe
        (SIFT) départage les meilleures candidates."""
        with self._verrou:
            scores = []
            for cle, g in self.vecteurs.items():
                if g is None or cle not in self.fiches:
                    continue
                sims = (vecteurs @ g.T).max(axis=0)
                scores.append((float(sims.max()), cle, sims))
            scores.sort(key=lambda s: -s[0])
            sortie, absentes = [], set()
            for rang, (s, cle, sims) in enumerate(scores):
                if sift is not None and rang < candidats:
                    s += BONUS_SIFT * min(self._sift_max(cle, sims, sift, absentes), PLAFOND_SIFT)
                sortie.append((cle, s))
            for cle in absentes:            # photo retirée à la main de la galerie
                self._lire_galerie(cle)
            return sorted(sortie, key=lambda x: -x[1])

    def _sift_max(self, cle, sims, sift, absentes):
        meilleur = 0
        for i in np.argsort(-sims)[:4]:
            p = self.dossier / cle / self.fichiers[cle][i]
            pts = self.points.get(str(p))
            if pts is None:
                image = lire_image(p)
                if image is None:
                    absentes.add(cle)
                    continue
                pts = self.points[str(p)] = vision.points_sift(image)
            meilleur = max(meilleur, vision.appariement_sift(sift[:2], pts))
            if len(sift) > 2:           # flanc opposé : la vue miroir, calculée par l'appelant
                meilleur = max(meilleur, vision.appariement_sift(sift[2], pts))
        return meilleur
