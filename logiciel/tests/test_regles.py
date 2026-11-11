"""Règles sans caméra : santé d'un bac, carnet de l'eau, lots d'élevage, suivi et étalonnage.
Lancer : python tests/test_regles.py"""
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eau
import elevage
import fiches
import fretille_cam as m
import i18n
import sante

ok = 0


def verifier(nom, condition, detail=""):
    global ok
    print(("OK   " if condition else "ÉCHEC") + f" {nom} {detail}")
    if not condition:
        raise SystemExit(1)
    ok += 1


def journee(debut_h, fin_h, **valeurs):
    base = {"nb": 8, "nb_max": 8, "surface": 0.1, "fond": 0.2, "activite": 0.03, "vitesse": 0.5, "lumiere": 120.0,
            "dispersion": 0.3}
    base.update(valeurs)
    return [{"heure": f"{h:02d}:{mi:02d}", **base} for h in range(debut_h, fin_h) for mi in range(60)]


MAINTENANT = datetime(2026, 11, 10, 15, 0)
HIER = {MAINTENANT.date() - timedelta(days=k): journee(8, 20) for k in range(1, 4)}
BAC = {"id": "b1", "nom": "Aquarium", "type": "aquarium", "surface": 0.1}

b = sante.bilan(BAC, journee(8, 16), HIER, MAINTENANT)
verifier("bac normal", b["etat"] == "ok" and not b["alertes"] and b["nb"] == 8 and b["attendus"] == 8, b)
b = sante.bilan(BAC, journee(8, 14) + journee(14, 16, surface=0.7), HIER, MAINTENANT)
verifier("poissons massés en surface : alerte oxygène", b["etat"] == "alerte" and b["alertes"][0][0].startswith("oxygene:b1:")
         and b["alertes"][0][3] == 5, b["raisons"])
b = sante.bilan({**BAC, "type": "bassin"}, journee(8, 14) + journee(14, 16, surface=0.7), HIER, MAINTENANT)
verifier("bassin vu de dessus : pas de règle de surface", not any(a[0].startswith("oxygene") for a in b["alertes"]))
b = sante.bilan(BAC, journee(8, 14) + journee(14, 16, nb=5, nb_max=5), HIER, MAINTENANT)
verifier("poissons non vus (habitude apprise)", any(a[0].startswith("manquants") and "3 poissons non vus" in a[1] for a in b["alertes"]),
         b["raisons"])
b = sante.bilan({**BAC, "nombre": 9}, journee(8, 16), HIER, MAINTENANT)
verifier("nombre indiqué : un poisson de moins compte", any(a[0].startswith("manquants") for a in b["alertes"]))
b = sante.bilan(BAC, journee(8, 14) + journee(14, 16, activite=0.005), HIER, MAINTENANT)
verifier("léthargie", any(a[0].startswith("lethargie") for a in b["alertes"]), b["raisons"])
b = sante.bilan(BAC, journee(8, 14) + journee(14, 16, activite=0.2), HIER, MAINTENANT)
verifier("agitation", any(a[0].startswith("agitation") for a in b["alertes"]))
b = sante.bilan(BAC, journee(8, 14) + journee(14, 16, fond=0.9), HIER, MAINTENANT)
verifier("poissons au fond", any(a[0].startswith("fond") for a in b["alertes"]))
b = sante.bilan(BAC, journee(8, 14) + journee(14, 16, lumiere=5.0, activite=0.0, nb=0, nb_max=0), HIER, MAINTENANT)
verifier("dans le noir : aucun jugement", b["etat"] == "inconnu" and not b["alertes"], b["raisons"])
b = sante.bilan(BAC, journee(8, 16), {}, MAINTENANT)
verifier("premier jour : en apprentissage, rien d'inventé", b["etat"] == "ok" and not b["alertes"]
         and "apprentissage" in b["raisons"][0], b["raisons"])
a1 = sante.bilan(BAC, journee(8, 14) + journee(14, 16, surface=0.7), HIER, MAINTENANT)["alertes"][0][0]
a2 = sante.bilan(BAC, journee(8, 14) + journee(14, 16, surface=0.7), HIER, MAINTENANT.replace(hour=16))["alertes"][0][0]
a3 = sante.bilan(BAC, journee(8, 19) + journee(19, 21, surface=0.7), HIER, MAINTENANT.replace(hour=20))["alertes"]
verifier("une alerte par quart de journée", a1 == a2 and (not a3 or a3[0][0] != a1))

# carnet de l'eau
d = Path(tempfile.mkdtemp(prefix="fretille_regles_"))
eau.ajouter(d, "b1", "2026-11-01", {"temp": "25,5", "ph": 7.2, "no2": 0.05, "no3": 20}, changement_pct="30")
eau.ajouter(d, "b1", "2026-11-09", {"no2": 0.6, "nh3": "", "temp": 31})
tests = eau.lire(d, "b1")
avis = eau.avis(tests, {"temp_min": 22, "temp_max": 28}, date(2026, 11, 20))
verifier("eau : NO2 dangereux et température trop haute", tests[0]["temp"] == 25.5
         and any(n == "alerte" and "NO2" in t for n, t in avis) and any(n == "surveiller" and "Température" in t for n, t in avis), avis)
verifier("eau : rappel du changement d'eau", any("changement d'eau il y a 19 jours" in t for _n, t in avis))
try:
    eau.ajouter(d, "b1", "2026-11-09", {"ph": "abc"})
    verifier("eau : valeur illisible refusée", False)
except ValueError:
    verifier("eau : valeur illisible refusée", True)
eau.retirer(d, "b1", "2026-11-09")
verifier("eau : test retiré", len(eau.lire(d, "b1")) == 1)

# élevage
e = elevage.Elevage(d)
lot = e.creer("Koï printemps", "Koï", "b2", "2026-04-01", 2000, ["k1", "k2"])
e.ajouter(lot, "2026-04-05", "eclosion", 1500)
e.ajouter(lot, "2026-05-01", "pertes", 300)
e.ajouter(lot, "2026-06-01", "tri", 700, note="écartés")
e.ajouter(lot, "2026-07-01", "transfert", bac="bassin")
r = elevage.bilan(e.lot(lot), date(2026, 7, 10))
verifier("lot : effectif, survie (tri à part), âge", r["effectif"] == 500 and r["survie"] == 0.625 and r["tries"] == 700 and r["age_j"] == 100
         and e.lot(lot)["bac"] == "bassin", r)
try:
    e.ajouter(lot, "2026-07-02", "vente", 900)
    verifier("lot : vendre plus que l'effectif refusé", False)
except ValueError:
    verifier("lot : vendre plus que l'effectif refusé", True)
verifier("lot : relu depuis le disque", elevage.Elevage(d).lot(lot)["evenements"][-1]["genre"] == "transfert")

# suivi
p = m.Piste((100, 100, 40, 20), 0.0)
for t in range(1, 30):
    p.ajouter((100 + (t % 2), 100, 40, 20), t * 30.0)
verifier("piste immobile mesurée", 800 <= p.immobile_depuis(30 * 30.0) <= 900, p.immobile_depuis(900.0))
p.ajouter((400, 100, 40, 20), 31 * 30.0)
verifier("piste qui repart : plus immobile", p.immobile_depuis(31 * 30.0) == 0)
pistes = [m.Piste((0, 0, 40, 20), 0), m.Piste((300, 0, 40, 20), 0)]
paires, nouvelles = m.associer(pistes, [(0.9, (310, 5, 40, 20)), (0.9, (20, 3, 40, 20)), (0.9, (600, 400, 40, 20))])
verifier("association au plus proche", len(paires) == 2 and paires[0][0] is not paires[1][0]
         and {id(pp): bb[1][0] for pp, bb in paires}[id(pistes[1])] == 310 and len(nouvelles) == 1)
verifier("étalonnage", abs(m.etalonner(0, 0, 300, 400, 50) - 0.1) < 1e-9)

# langues : un catalogue traduit est utilisé, une clé absente retombe sur le français
verifier("français par défaut", fiches.age_lisible("2024-01", date(2026, 3, 20)) == "2 ans et 2 mois"
         and fiches.date_lisible("2019-03") == "mars 2019", fiches.age_lisible("2024-01", date(2026, 3, 20)))
try:
    i18n.langue = "en"
    i18n._catalogue = {"{n} an": "{n} year", "{n} ans": "{n} years", "{n} mois": "{n} months",
                       "{ans} et {mois}": "{ans} and {mois}", "mars": "March",
                       "Encore en apprentissage des habitudes du bac (un jour de mesures)": "Still learning this tank's habits"}
    verifier("anglais : âge traduit (pluriels)", fiches.age_lisible("2024-01", date(2026, 3, 20)) == "2 years and 2 months",
             fiches.age_lisible("2024-01", date(2026, 3, 20)))
    verifier("anglais : mois traduit, ordre de la clé absente gardé", fiches.date_lisible("2019-03") == "March 2019",
             fiches.date_lisible("2019-03"))
    b = sante.bilan(BAC, journee(8, 16), {}, MAINTENANT)
    verifier("anglais : raison de santé traduite", b["raisons"] == ["Still learning this tank's habits"], b["raisons"])
    verifier("anglais : clé absente du catalogue en français", fiches.age_lisible("2026-02", date(2026, 3, 20)) == "1 mois"
             and fiches.age_lisible("2026-03-10", date(2026, 3, 20)) == "moins d'un mois",
             fiches.age_lisible("2026-03-10", date(2026, 3, 20)))
finally:
    i18n.choisir("fr")
verifier("retour au français", fiches.date_lisible("2019-03") == "mars 2019" and i18n.langue == "fr")
print(f"\n{ok} vérifications réussies")
