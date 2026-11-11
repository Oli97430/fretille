"""
Frétille — santé d'un bac, d'après les mesures minute par minute (fretille_cam.lire_mesures).

On compare la dernière demi-heure aux mêmes heures des jours précédents : chaque bac a ses
habitudes (des corydoras au fond, des koï qui viennent en surface quand on approche), seul un
changement compte. Rien n'est jugé dans le noir : poissons cachés et immobiles, c'est la nuit.

  - manque d'oxygène : poissons massés sous la surface (aquarium, ligne d'eau réglée) ;
  - poissons non vus : moins de poissons que d'habitude, ou que le nombre indiqué ;
  - léthargie : activité très en dessous de l'habitude ;
  - agitation : activité très au-dessus (prédateur, bagarre, eau qui pique) ;
  - poissons au fond, alors que d'habitude ils nagent.

Fonctions pures : testées dans tests/test_regles.py.
"""
from datetime import datetime, timedelta
from statistics import median

from i18n import nombre, pourcent, t, tn

FENETRE_MIN = 30
LUMIERE_MIN = 25.0              # en dessous (gris moyen sur 255), c'est la nuit : pas de jugement
LIGNES_MIN = 10                 # minutes mesurées qu'il faut dans la fenêtre
REFERENCE_MIN = 30              # minutes de référence qu'il faut les jours précédents


def _minutes(heure):
    h, m = heure.split(":")[:2]
    return int(h) * 60 + int(m)


def fenetre(lignes, maintenant, minutes=FENETRE_MIN):
    """Lignes du jour des `minutes` dernières minutes, éclairées."""
    fin = maintenant.hour * 60 + maintenant.minute
    return [l for l in lignes if fin - minutes <= _minutes(l["heure"]) <= fin and l["lumiere"] >= LUMIERE_MIN]


def reference(historique, maintenant, marge=60):
    """Lignes éclairées des jours précédents, à la même heure (± marge minutes)."""
    centre = maintenant.hour * 60 + maintenant.minute
    sortie = []
    for _jour, lignes in historique.items():
        sortie += [l for l in lignes if abs(_minutes(l["heure"]) - centre) <= marge and l["lumiere"] >= LUMIERE_MIN]
    return sortie


def _med(lignes, cle):
    return median(l[cle] for l in lignes) if lignes else 0.0


def _p90(valeurs):
    v = sorted(valeurs)
    return v[min(len(v) - 1, int(0.9 * len(v)))] if v else 0


def bilan(bac, lignes_du_jour, historique, maintenant=None):
    """{"etat": "ok"|"surveiller"|"alerte"|"inconnu", "raisons": [texte], "alertes": [(épisode, titre,
    message, priorité ntfy 1-5)], "nb": poissons vus, "attendus": poissons attendus}.
    historique : {date: lignes} des jours précédents (7 au plus utiles)."""
    maintenant = maintenant or datetime.now()
    nom = bac.get("nom", bac["id"])
    recentes = fenetre(lignes_du_jour, maintenant)
    ref = reference(historique, maintenant)
    res = {"etat": "inconnu", "raisons": [], "alertes": [], "nb": None, "attendus": None}
    if len(recentes) < LIGNES_MIN:
        dernieres = fenetre(lignes_du_jour, maintenant, 10) or [l for l in lignes_du_jour[-10:]]
        if dernieres and all(l["lumiere"] < LUMIERE_MIN for l in lignes_du_jour[-10:]):
            res["raisons"].append(t("Bac dans le noir : rien à juger"))
        else:
            res["raisons"].append(t("Pas assez de mesures récentes"))
        return res
    res["etat"] = "ok"
    bloc = f"{maintenant:%Y-%m-%d}-{maintenant.hour // 6}"     # une alerte par sujet et par quart de journée
    assez_ref = len(ref) >= REFERENCE_MIN

    def signaler(niveau, sujet, titre, message, priorite):
        res["raisons"].append(message)
        if niveau == "alerte" or res["etat"] == "ok":
            res["etat"] = niveau
        res["alertes"].append((f"{sujet}:{bac['id']}:{bloc}", t("{bac} : {titre}", bac=nom, titre=titre), message, priorite))

    # manque d'oxygène : massés sous la surface
    if bac.get("surface") is not None and bac.get("type", "aquarium") != "bassin":
        recent_surf = [l for l in recentes if _minutes(l["heure"]) >= maintenant.hour * 60 + maintenant.minute - 15]
        s = _med(recent_surf or recentes, "surface")
        habitude = _med(ref, "surface") if assez_ref else 0.15
        if s >= 0.5 and habitude < 0.25 and _med(recentes, "nb") >= 2:
            signaler("alerte", "oxygene", t("poissons en surface"),
                     t("{part} des poissons restent sous la surface (d'habitude {habitude}) : manque d'oxygène ? "
                       "Vérifiez le bulleur, la pompe et la température.", part=pourcent(s), habitude=pourcent(habitude)), 5)

    # poissons non vus
    vus = max(l["nb_max"] for l in recentes)
    res["nb"] = int(vus)
    attendus = int(bac.get("nombre") or 0)
    if attendus:                        # nombre indiqué : en petit nombre, chaque poisson compte (un koï disparu)
        tolerance = 0 if attendus <= 12 else round(0.1 * attendus)
    elif assez_ref:
        attendus = int(_p90([l["nb_max"] for l in ref]))
        tolerance = max(1, round(0.25 * attendus))
    else:
        tolerance = 0
    res["attendus"] = attendus or None
    if attendus >= 1 and vus < attendus - tolerance:
        manque = attendus - int(vus)
        signaler("surveiller", "manquants", tn("{n} poisson non vu", "{n} poissons non vus", manque),
                 tn("{n} poisson vu en 30 min, {attendus} attendus. Caché, malade, sauté hors du bac, ou pris par un prédateur ?",
                    "{n} poissons vus en 30 min, {attendus} attendus. Caché, malade, sauté hors du bac, ou pris par un prédateur ?",
                    int(vus), attendus=attendus), 4)

    if assez_ref:
        a, a_ref = _med(recentes, "activite"), _med(ref, "activite")
        if a_ref >= 0.002 and a < 0.35 * a_ref:
            signaler("surveiller", "lethargie", t("poissons peu actifs"),
                     t("Activité {part} de l'habitude à cette heure : poissons apathiques ? "
                       "Vérifiez la température et l'eau (NH3, NO2).", part=pourcent(a / a_ref)), 4)
        elif a > 3 * max(a_ref, 0.002) and a > 0.02:
            signaler("surveiller", "agitation", t("poissons agités"),
                     t("Activité {fois} fois l'habitude : prédateur, bagarre, ponte, ou eau irritante ?",
                       fois=nombre(a / max(a_ref, 1e-6))), 3)
        f, f_ref = _med(recentes, "fond"), _med(ref, "fond")
        if f >= 0.7 and f_ref < 0.35 and _med(recentes, "nb") >= 2:
            signaler("surveiller", "fond", t("poissons au fond"),
                     t("{part} des poissons restent au fond (d'habitude {habitude}) : stress ou maladie ?",
                       part=pourcent(f), habitude=pourcent(f_ref)), 3)
    elif not res["raisons"]:
        res["raisons"].append(t("Encore en apprentissage des habitudes du bac (un jour de mesures)"))
    if res["etat"] == "ok" and not res["raisons"]:
        res["raisons"].append(t("Comportement habituel"))
    return res


def historique_jours(lire, bac_id, aujourd_hui, jours=7):
    """{date: lignes} des jours précédents, avec lire(bac_id, date) -> lignes."""
    return {aujourd_hui - timedelta(days=k): lire(bac_id, aujourd_hui - timedelta(days=k)) for k in range(1, jours + 1)}


def serie_horaire(lignes, cle):
    """[(heure 0-23, moyenne)] pour un graphique de la journée."""
    par_heure = {}
    for l in lignes:
        par_heure.setdefault(_minutes(l["heure"]) // 60, []).append(l[cle])
    return [(h, sum(v) / len(v)) for h, v in sorted(par_heure.items())]
