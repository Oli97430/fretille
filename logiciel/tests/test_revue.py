"""Non-régression de la première revue de code de Frétille : un essai par défaut corrigé.
Lancer : python tests/test_revue.py"""
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eau
import elevage
import fretille_cam as m
from outils import ajouter_csv, lire_csv

ok = 0


def verifier(nom, condition, detail=""):
    global ok
    print(("OK   " if condition else "ÉCHEC") + f" {nom} {detail}")
    if not condition:
        raise SystemExit(1)
    ok += 1


d = Path(tempfile.mkdtemp(prefix="fretille_revue_"))

# élevage : le téléphone écrit pendant que le PC garde sa copie
pc = elevage.Elevage(d)
lot = pc.creer("Lot", "Koï", "b1", "2026-10-01", 100)
elevage.Elevage(d).ajouter(lot, "2026-10-10", "pertes", 10)          # le téléphone
pc.ajouter(lot, "2026-10-11", "vente", 5)                            # le PC, avec sa vieille copie
verifier("élevage : la saisie du téléphone n'est pas écrasée",
         [e["genre"] for e in elevage.Elevage(d).lot(lot)["evenements"]] == ["pertes", "vente"])
try:
    pc.ajouter(lot, "2026-10-05", "pertes", 90)                      # rendrait la vente du 11 impossible
    verifier("élevage : retrait antidaté incohérent refusé", False)
except ValueError:
    verifier("élevage : retrait antidaté incohérent refusé", True)
pc.ajouter(lot, "2026-10-12", "transfert", bac="b2")
evts = pc.lot(lot)["evenements"]
pc.retirer(lot, len(evts) - 1, evts[-1])
verifier("élevage : retirer un transfert ramène le lot à son bac", pc.lot(lot)["bac"] == "b1")
pc.ajouter(lot, "2026-10-13", "tri", 35)
verifier("élevage : le tri ne compte pas comme survivants", elevage.bilan(pc.lot(lot))["survie"] == 55 / 65)

# eau : même jour complété, chaque paramètre lu sur son dernier test
eau.ajouter(d, "b1", "2026-11-01", {"no2": 0.8, "ph": 7})
eau.ajouter(d, "b1", "2026-11-01", {}, changement_pct=50)
t = eau.lire(d, "b1")[0]
verifier("eau : même jour complété, rien d'effacé", t.get("no2") == 0.8 and t.get("changement") == 50)
eau.ajouter(d, "b1", "2026-11-02", {}, changement_pct=30)
avis = eau.avis(eau.lire(d, "b1"), {}, date(2026, 11, 3))
verifier("eau : un simple changement d'eau ne cache pas un NO2 dangereux", any(n == "alerte" and "NO2" in x for n, x in avis), avis)

# poisson immobile plus longtemps que la mémoire du trajet
p = m.Piste((100, 100, 40, 20), 0.0)
for k in range(1, 2000):
    p.ajouter((100, 100, 40, 20), k * 4.0)
verifier("immobile mesuré au-delà de 400 analyses", p.immobile_depuis(2000 * 4.0) >= 7900, p.immobile_depuis(8000.0))
p.ajouter((300, 100, 40, 20), 8004.0)
verifier("immobile remis à zéro quand il bouge", p.immobile_depuis(8004.0) == 0 and not p.immobile_note)

# vote en cours d'ajout par l'autre fil
p.votes["x"]                                                        # liste vide créée, pas encore remplie
verifier("vote vide ignoré (pas d'erreur entre deux fils)", p.probable() is None)

# journal à l'ancien en-tête : mis de côté
chemin = d / "mesures.csv"
chemin.write_text("heure;nb\n10:00;3\n", encoding="utf-8")
ajouter_csv(chemin, m.COLONNES_MESURES, {"heure": "10:01", "nb": 4, "dispersion": 0.2})
verifier("ancien en-tête mis de côté, nouvelle colonne gardée", (d / "mesures.ancien.csv").exists()
         and lire_csv(chemin)[0]["dispersion"] == "0.2")

# identifiants d'événement uniques, même dans la même seconde
s = m.Surveillance({"id": "b1", "nom": "Bac", "source": "0"}, d)
vus = []
s.sur_evenement = vus.append
fils = [threading.Thread(target=s._evenement, args=("info", "t", "d")) for _ in range(5)]
for f in fils:
    f.start()
for f in fils:
    f.join()
verifier("identifiants d'événement uniques", len({e["id"] for e in vus}) == 5)
chemin_prevu = s.enregistrer(30, "Héron")
verifier("clip d'intrus nommé dès la demande", chemin_prevu.startswith("bacs/b1/clips/") and s.chemin_prevu is not None)
print(f"\n{ok} vérifications réussies")
