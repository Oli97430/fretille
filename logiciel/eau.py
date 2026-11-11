"""
Frétille — carnet de l'eau d'un bac : tests (température, pH, NH3/NH4, NO2, NO3, GH, KH) et
changements d'eau, avec les seuils qui inquiètent. bacs/<id>/eau.json.

Seuils d'eau douce courants ; l'utilisateur garde la main (plage de température par bac).
"""
from datetime import date

from i18n import nombre, t, tn
from outils import ecrire_json, lire_json

PARAMETRES = [
    # (clé, libellé, unité, min conseillé, max conseillé, max alarmant)
    ("temp", "Température", "°C", None, None, None),        # plage propre au bac
    ("ph", "pH", "", 6.0, 8.5, None),
    ("nh3", "NH3/NH4", "mg/L", None, 0.1, 0.5),
    ("no2", "NO2", "mg/L", None, 0.1, 0.5),
    ("no3", "NO3", "mg/L", None, 50, 100),
    ("gh", "GH", "°d", 3, 20, None),
    ("kh", "KH", "°d", 2, 15, None),
]
RAPPEL_CHANGEMENT_J = 14


def libelle(nom):
    """Libellé affiché d'un paramètre de PARAMETRES (clé française) ; les formules chimiques restent telles quelles."""
    return t("Température") if nom == "Température" else t(nom)


def chemin(dossier, bac_id):
    from fretille_cam import dossier_bac
    return dossier_bac(dossier, bac_id) / "eau.json"


def lire(dossier, bac_id):
    """Tests du plus ancien au plus récent."""
    return sorted(lire_json(chemin(dossier, bac_id), []), key=lambda t: t["date"])


def ajouter(dossier, bac_id, jour, valeurs, changement_pct=None, note=""):
    """valeurs : {clé: nombre} (les absentes ne sont pas testées). Remplace un test du même jour."""
    jour = date.fromisoformat(str(jour)).isoformat()
    if date.fromisoformat(jour) > date.today():
        raise ValueError(t("date dans le futur"))
    propres = {}
    for cle, v in valeurs.items():
        if v in (None, ""):
            continue
        v = float(str(v).replace(",", "."))
        if not -5 <= v <= 1000:
            nom = next((p[1] for p in PARAMETRES if p[0] == cle), cle)
            raise ValueError(t("valeur impossible pour {parametre} : {valeur}", parametre=libelle(nom), valeur=nombre(v, 3)))
        propres[cle] = v
    if changement_pct not in (None, ""):
        changement_pct = float(str(changement_pct).replace(",", ".").rstrip("%"))
        if not 0 < changement_pct <= 100:
            raise ValueError(t("changement d'eau entre 1 et 100 %"))
    else:
        changement_pct = None
    if not propres and changement_pct is None and not note.strip():
        raise ValueError(t("rien à noter"))
    tests = lire(dossier, bac_id)
    ancien = next((t for t in tests if t["date"] == jour), {})     # même jour : on complète, sans rien effacer
    tests = [t for t in tests if t["date"] != jour]
    nouveau = {**ancien, "date": jour, **propres}
    if changement_pct:
        nouveau["changement"] = changement_pct
    if note.strip():
        nouveau["note"] = (ancien.get("note", "") + " · " if ancien.get("note") else "") + note.strip()
    tests.append(nouveau)
    ecrire_json(chemin(dossier, bac_id), sorted(tests, key=lambda t: t["date"]))


def retirer(dossier, bac_id, jour):
    ecrire_json(chemin(dossier, bac_id), [t for t in lire(dossier, bac_id) if t["date"] != jour])


def avis(tests, bac, aujourd_hui=None):
    """[(niveau "alerte"|"surveiller"|"info", message)] sur le dernier test et les changements d'eau."""
    aujourd_hui = aujourd_hui or date.today()
    sortie = []
    if not tests:
        return [("info", t("Aucun test d'eau noté"))]
    for cle, nom, unite, mini, maxi, alarmant in PARAMETRES:
        mesure = next((t for t in reversed(tests) if t.get(cle) is not None), None)   # son dernier test à lui
        if mesure is None:
            continue
        v = mesure[cle]
        if cle == "temp":
            mini, maxi = bac.get("temp_min"), bac.get("temp_max")
        u = f" {unite}" if unite else ""
        valeur = f"{nombre(v, 3)}{u}"
        if alarmant is not None and v >= alarmant:
            sortie.append(("alerte", t("{parametre} à {valeur} : dangereux, changez une partie de l'eau aujourd'hui",
                                       parametre=libelle(nom), valeur=valeur)))
        elif maxi is not None and v > maxi:
            sortie.append(("surveiller", t("{parametre} à {valeur} : au-dessus de {limite}",
                                           parametre=libelle(nom), valeur=valeur, limite=f"{nombre(maxi, 3)}{u}")))
        elif mini is not None and v < mini:
            sortie.append(("surveiller", t("{parametre} à {valeur} : en dessous de {limite}",
                                           parametre=libelle(nom), valeur=valeur, limite=f"{nombre(mini, 3)}{u}")))
    changements = [t["date"] for t in tests if t.get("changement")]
    if changements:
        jours = (aujourd_hui - date.fromisoformat(changements[-1])).days
        if jours >= RAPPEL_CHANGEMENT_J:
            sortie.append(("info", tn("Dernier changement d'eau il y a {n} jour", "Dernier changement d'eau il y a {n} jours", jours)))
    avec_valeurs = [t for t in tests if any(t.get(p[0]) is not None for p in PARAMETRES)]
    age = (aujourd_hui - date.fromisoformat(avec_valeurs[-1]["date"])).days if avec_valeurs else 0
    if avec_valeurs and age >= 30:
        sortie.append(("info", tn("Dernier test il y a {n} jour", "Dernier test il y a {n} jours", age)))
    return sortie or [("ok", t("Eau dans les normes au dernier test"))]
