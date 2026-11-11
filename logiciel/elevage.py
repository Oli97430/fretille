"""
Frétille — élevage : lots d'alevins (une ponte, ses parents, son bac) et leur suivi.

elevage.json : [{"id", "nom", "espece", "bac", "ponte": date, "parents": [clés], "nombre": œufs ou
alevins au départ, "clos": bool, "evenements": [{"date", "genre", "nombre", "bac", "note"}]}]

Genres d'événement : eclosion (fixe l'effectif), comptage (fixe l'effectif), pertes, vente,
cession (retirent), transfert (change de bac), tri (retire les écartés), note.
"""
import threading
import time
from datetime import date
from pathlib import Path

from i18n import t, tn
from outils import ecrire_json, lire_json

GENRES = ("eclosion", "comptage", "pertes", "vente", "cession", "tri", "transfert", "note")
LIBELLES = {"eclosion": "Éclosion", "comptage": "Comptage", "pertes": "Pertes", "vente": "Vente",
            "cession": "Cession", "tri": "Tri", "transfert": "Transfert", "note": "Note"}
FIXENT = ("eclosion", "comptage")
RETIRENT = ("pertes", "vente", "cession", "tri")


def libelle(genre):
    """Libellé affiché d'un genre d'événement (LIBELLES reste en français)."""
    return t(LIBELLES.get(genre, genre))


def _cles_a_traduire():
    """Jamais appelée : signale à tests/cles_i18n.py les libellés de LIBELLES, traduits par libelle()."""
    return (t("Éclosion"), t("Comptage"), t("Pertes"), t("Vente"), t("Cession"), t("Tri"), t("Transfert"), t("Note"))


VERROU = threading.RLock()      # le PC et le téléphone écrivent le même fichier


class Elevage:
    """Chaque modification relit le fichier sous le verrou avant d'écrire : une saisie faite sur le
    téléphone entre-temps n'est jamais écrasée par la copie en mémoire du PC."""

    def __init__(self, dossier):
        self.chemin = Path(dossier) / "elevage.json"
        self.lots = lire_json(self.chemin, [])

    def recharger(self):
        with VERROU:
            self.lots = lire_json(self.chemin, [])
        return self.lots

    def _ecrire(self):
        ecrire_json(self.chemin, self.lots)

    def lot(self, ident):
        return next((l for l in self.lots if l["id"] == ident), None)

    def creer(self, nom, espece, bac, ponte, nombre, parents=(), note=""):
        with VERROU:
            self.recharger()
            return self._creer(nom, espece, bac, ponte, nombre, parents, note)

    def _creer(self, nom, espece, bac, ponte, nombre, parents, note):
        ponte = date.fromisoformat(str(ponte)).isoformat()
        nombre = int(nombre)
        if nombre < 0 or nombre > 1_000_000:
            raise ValueError(t("nombre impossible"))
        if not nom.strip():
            raise ValueError(t("nom vide"))
        ident = f"l{int(time.time() * 1000):x}"
        while self.lot(ident):
            ident += "x"
        self.lots.append({"id": ident, "nom": nom.strip(), "espece": espece, "bac": bac, "bac_depart": bac, "ponte": ponte,
                          "parents": list(parents), "nombre": nombre, "clos": False, "note": note.strip(),
                          "evenements": []})
        self._ecrire()
        return ident

    def ajouter(self, ident, jour, genre, nombre=None, bac=None, note=""):
        with VERROU:
            self.recharger()
            self._ajouter(ident, jour, genre, nombre, bac, note)

    def _ajouter(self, ident, jour, genre, nombre, bac, note):
        lot = self.lot(ident)
        if lot is None:
            raise KeyError(ident)
        if genre not in GENRES:
            raise ValueError(t("genre inconnu : {genre}", genre=genre))
        jour = date.fromisoformat(str(jour)).isoformat()
        if jour < lot["ponte"]:
            raise ValueError(t("date avant la ponte"))
        if genre in FIXENT + RETIRENT:
            if nombre in (None, ""):
                raise ValueError(t("nombre manquant"))
            nombre = int(nombre)
            if nombre < 0:
                raise ValueError(t("nombre négatif"))
            if genre in RETIRENT and nombre > effectif(lot, jour):
                raise ValueError(tn("il n'y a que {n} alevin", "il n'y a que {n} alevins", effectif(lot, jour)))
            # un retrait daté dans le passé ne doit pas rendre impossible un retrait déjà noté après lui
            essai = sorted(lot["evenements"] + [{"date": jour, "genre": genre, "nombre": nombre}], key=lambda x: x["date"])
            if not _coherent(lot["nombre"], essai):
                raise ValueError(t("ce retrait rend impossible un retrait noté plus tard (effectif négatif)"))
        if genre == "transfert" and not bac:
            raise ValueError(t("bac de destination manquant"))
        e = {"date": jour, "genre": genre, "note": note.strip()}
        if nombre not in (None, ""):
            e["nombre"] = int(nombre)
        if bac:
            e["bac"] = bac
        lot["evenements"] = sorted(lot["evenements"] + [e], key=lambda x: x["date"])
        if genre == "transfert":
            lot["bac"] = bac
        self._ecrire()

    def retirer(self, ident, rang, attendu=None):
        """Retire l'événement n° rang ; attendu : celui affiché (s'il a changé de rang entre-temps,
        c'est lui qu'on retire, et rien s'il a disparu)."""
        with VERROU:
            self.recharger()
            lot = self.lot(ident)
            if not lot:
                return
            evts = lot["evenements"]
            if attendu is not None and not (0 <= rang < len(evts) and evts[rang] == attendu):
                rang = next((i for i, e in enumerate(evts) if e == attendu), -1)
            if 0 <= rang < len(evts):
                retire = evts.pop(rang)
                if retire["genre"] == "transfert":     # retour au bac d'avant ce transfert
                    transferts = [e for e in evts if e["genre"] == "transfert"]
                    lot["bac"] = transferts[-1]["bac"] if transferts else lot.get("bac_depart", lot["bac"])
                self._ecrire()

    def clore(self, ident, clos=True):
        with VERROU:
            self.recharger()
            lot = self.lot(ident)
            if lot:
                lot["clos"] = clos
                self._ecrire()

    def supprimer(self, ident):
        with VERROU:
            self.recharger()
            self.lots = [l for l in self.lots if l["id"] != ident]
            self._ecrire()


def _coherent(depart, evenements):
    n = depart
    for e in evenements:
        if e["genre"] in FIXENT:
            n = e["nombre"]
        elif e["genre"] in RETIRENT:
            n -= e["nombre"]
            if n < 0:
                return False
    return True


def effectif(lot, jusqu_au=None):
    """Nombre d'alevins à une date (aujourd'hui par défaut)."""
    n = lot["nombre"]
    for e in lot["evenements"]:
        if jusqu_au and e["date"] > str(jusqu_au):
            break
        if e["genre"] in FIXENT:
            n = e["nombre"]
        elif e["genre"] in RETIRENT:
            n = max(0, n - e["nombre"])
    return n


def bilan(lot, aujourd_hui=None):
    """{"effectif", "age_j", "survie", "vendus", "pertes", "tries"}. La survie compte à partir de
    l'éclosion (ou du nombre de départ) : part des alevins encore vivants, vendus et cédés compris ;
    les écartés au tri ne comptent ni comme survivants ni comme morts."""
    aujourd_hui = aujourd_hui or date.today()
    base, n, pertes, vendus, tries = lot["nombre"], lot["nombre"], 0, 0, 0
    for e in lot["evenements"]:
        if e["genre"] == "eclosion":
            base = n = e["nombre"]
            pertes = vendus = tries = 0
        elif e["genre"] == "comptage":
            n = e["nombre"]
        elif e["genre"] == "pertes":
            pertes += e["nombre"]
            n = max(0, n - e["nombre"])
        elif e["genre"] in ("vente", "cession"):
            vendus += e["nombre"]
            n = max(0, n - e["nombre"])
        elif e["genre"] == "tri":
            tries += e["nombre"]
            n = max(0, n - e["nombre"])
    survie = min(1.0, (n + vendus) / (base - tries)) if base - tries > 0 else None
    return {"effectif": n, "age_j": (aujourd_hui - date.fromisoformat(lot["ponte"])).days,
            "survie": survie, "vendus": vendus, "pertes": pertes, "tries": tries}
