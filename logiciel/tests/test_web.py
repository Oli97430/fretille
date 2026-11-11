"""Accès téléphone (web.py) : code, cookie, hôte, état des bacs, événements, direct par bac, fiches,
mesure, carnet, photo, lots, clips. Lancer : python tests/test_web.py"""
import http.client
import io
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PIL import Image

import elevage
import fretille_cam as m
import i18n
import registre as reg
import web
from outils import ajouter_csv

ok = 0


def verifier(nom, condition, detail=""):
    global ok
    print(("OK   " if condition else "ÉCHEC") + f" {nom} {detail}")
    if not condition:
        raise SystemExit(1)
    ok += 1


CODE = "12345678"
d = Path(tempfile.mkdtemp(prefix="fretille_web_"))
r = reg.Registre(d, descripteur=object())
kohaku = r.creer("Kohaku", "Koï")
r.modifier(kohaku, naissance="2021", sexe="Femelle", bac="b1", signes="tache en cœur sur la tête")
r.ajouter_mesure(kohaku, "2026-10-01", cm=31.5, grammes=480)
anonyme = r.creer("Koï n°2", "Koï", provisoire=True)

# un lot d'alevins, un événement de prédateur avec sa photo, un clip
lots = elevage.Elevage(d)
lot = lots.creer("Ponte guppys", "Guppy", "b2", (date.today() - timedelta(days=20)).isoformat(), 100)
photos = m.dossier_bac(d, "b1") / "photos"
photos.mkdir(parents=True)
Image.new("RGB", (640, 360), (20, 60, 80)).save(photos / "2026-11-09_06-12-00_b1_3.jpg")
clips = m.dossier_bac(d, "b1") / "clips"
clips.mkdir(parents=True)
(clips / "2026-11-09_06-11-55.mp4").write_bytes(b"0123456789")
ajouter_csv(d / "evenements.csv", m.COLONNES_EVENEMENTS,
            {"id": "2026-11-09_06-12-00_b1_3", "date": "2026-11-09", "heure": "06:12:00", "bac": "b1",
             "genre": "predateur", "titre": "Héron au bassin", "detail": "<script>x</script>",
             "photo": "bacs/b1/photos/2026-11-09_06-12-00_b1_3.jpg",
             "video": "bacs/b1/clips/2026-11-09_06-11-55.mp4", "cle": ""})
ajouter_csv(d / "evenements.csv", m.COLONNES_EVENEMENTS,
            {"id": "e2", "date": "2026-11-09", "heure": "07:00:00", "bac": "b2", "genre": "eau",
             "titre": "Eau changée", "detail": "", "photo": "", "video": "", "cle": ""})
(d / "secret.txt").write_text("privé")

image = np.full((720, 1280, 3), 90, np.uint8)
etat = {"pistes": [(1, (400, 300, 200, 150), "Kohaku", True, kohaku), (2, (10, 10, 50, 50), "", True, None)],
        "intrus": [((800, 100, 300, 200), "Héron", True)]}
BACS = [{"id": "b1", "nom": "Bassin", "type": "bassin", "etat": "alerte", "raisons": ["Héron vu à 06:12"],
         "nb": 6, "attendus": 7},
        {"id": "b2", "nom": "Nurserie", "type": "elevage", "etat": "arrete", "raisons": [], "nb": None, "attendus": None}]
modifs = []
s = web.Serveur(r, lambda: d, lambda: BACS, lambda bac: (image, etat) if bac == "b1" else None, CODE,
                apres_modif=lambda: modifs.append(1), port=0, hote="127.0.0.1").demarrer()
COOKIE = {"Cookie": f"fretille={s.jeton}"}
JSON_ = {**COOKIE, "Content-Type": "application/json"}


def req(methode, chemin, corps=None, entetes=None, hote=None):
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
    c.putrequest(methode, chemin, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", hote or f"127.0.0.1:{s.port}")
    for k, v in (entetes or {}).items():
        c.putheader(k, v)
    if corps is not None:
        corps = corps.encode() if isinstance(corps, str) else corps
        if "Content-Length" not in (entetes or {}):
            c.putheader("Content-Length", str(len(corps)))
    c.endheaders(corps)
    rep = c.getresponse()
    donnees = rep.read() if "mjpg" not in chemin else rep.read1(200000)
    c.close()
    return rep.status, dict(rep.getheaders()), donnees


# --- accès
verifier("nouveaux codes à 8 chiffres", len(web.nouveau_code()) == 8 and web.nouveau_code().isdigit())
st, _, corps = req("GET", "/")
verifier("sans code : page de code", st == 200 and b'name="code"' in corps and b"direct.mjpg" not in corps)
st, *_ = req("GET", "/api/etat")
verifier("sans code : données refusées", st == 403)
st, ent, _ = req("GET", f"/?code={CODE}")
cookie = ent.get("Set-Cookie", "")
verifier("lien du QR : jeton posé, pas le code", st == 303 and f"fretille={s.jeton}" in cookie and CODE not in cookie
         and "HttpOnly" in cookie and "SameSite=Strict" in cookie)
st, ent, _ = req("POST", "/connexion", f"code={CODE}", {"Content-Type": "application/x-www-form-urlencoded"})
verifier("code saisi : jeton posé", st == 303 and f"fretille={s.jeton}" in ent.get("Set-Cookie", ""))
st, *_ = req("GET", "/api/etat", entetes={"Cookie": f"fretille={CODE}"})
verifier("le code seul en cookie ne suffit pas", st == 403)
st, _, corps = req("GET", "/", entetes=COOKIE)
verifier("avec cookie : page complète", st == 200 and b"direct.mjpg" in corps and "Frétille".encode() in corps)
verifier("page : pas de onclick à valeur interpolée", b"onclick=" not in corps and b"onerror=" not in corps)

# --- langues de la page
verifier("page : chaque texte T(\"...\") est au dictionnaire, langue posée",
         "Mes poissons" in web.CLES_PAGE and "{n} photos ajoutées" in web.CLES_PAGE
         and all(f'T("{c}"' in web.PAGE for c in web.CLES_PAGE) and b'<html lang="fr">' in corps
         and b"/*TRADUCTIONS*/" not in corps and b"__LANGUE__" not in corps)
try:
    i18n.langue, i18n._catalogue = "en", {"Mes poissons": "My fish", "Entrer": "Enter <b>"}
    st, _, corps = req("GET", "/", entetes=COOKIE)
    page_en = corps.decode()
    tr = json.loads(page_en.split("const TR = ", 1)[1].split(";\n", 1)[0])
    verifier("page en anglais : traductions posées, lang=\"en\"", st == 200 and tr["Mes poissons"] == "My fish"
             and tr["Alerte"] == "Alerte" and '<html lang="en">' in page_en and 'const LANGUE = "en"' in page_en
             and 'const DECIMALE = "12.5"' in page_en)
    st, _, corps = req("GET", "/")
    verifier("page du code traduite et échappée", b'lang="en"' in corps and b"Enter &lt;b&gt;" in corps and b"<b>" not in corps)
    i18n._catalogue = {"Mes poissons": "</script><script>alert(1)</script><!--", "Alerte": " x"}
    st, _, corps = req("GET", "/", entetes=COOKIE)
    page_piege = corps.decode()
    tr = json.loads(page_piege.split("const TR = ", 1)[1].split(";\n", 1)[0])
    verifier("traduction piégée : pas de sortie de la balise <script>",
             page_piege.count("</script>") == 1 and page_piege.count("<script>") == 1 and "<!--" not in page_piege
             and " " not in page_piege and tr["Mes poissons"].startswith("</script>") and tr["Alerte"] == " x")
finally:
    i18n.choisir("fr")
st, _, corps = req("GET", "/", entetes=COOKIE)
verifier("retour au français", b'<html lang="fr">' in corps and b'"Mes poissons": "Mes poissons"' in corps)
st, *_ = req("GET", "/api/etat", entetes=COOKIE, hote="fretille.attaquant.example:8643")
verifier("nom de domaine refusé (DNS rebinding)", st == 421)

# --- état
st, _, corps = req("GET", "/api/etat", entetes=COOKIE)
e = json.loads(corps)
verifier("état : bacs", st == 200 and [b["id"] for b in e["bacs"]] == ["b1", "b2"] and e["bacs"][0]["etat"] == "alerte"
         and e["bacs"][0]["raisons"] == ["Héron vu à 06:12"])
ev = e["evenements"]
verifier("état : événements, le plus récent d'abord", [v["id"] for v in ev] == ["e2", "2026-11-09_06-12-00_b1_3"]
         and ev[1]["genre"] == "predateur" and ev[1]["photo"] == "2026-11-09_06-12-00_b1_3.jpg"
         and ev[1]["video"] == "2026-11-09_06-11-55.mp4" and ev[1]["nom_bac"] == "Bassin", ev)
p0 = e["poissons"][0]
verifier("état : poissons", p0["cle"] == kohaku and p0["nom"] == "Kohaku" and p0["taille"] == "31,5 cm"
         and p0["poids"] == "480 g" and p0["bac"] == "b1" and "Koï" in p0["identite"]
         and e["poissons"][1]["cle"] == anonyme, p0)
verifier("état : lots", e["lots"] == [{"id": lot, "nom": "Ponte guppys", "espece": "Guppy", "bac": "b2", "nom_bac": "Nurserie",
                                         "effectif": 100, "age_j": 20, "survie": 1.0}], e["lots"])
verifier("état : clips", e["clips"] == ["bacs/b1/clips/2026-11-09_06-11-55.mp4"], e["clips"])

# --- direct par bac
st, ent, corps = req("GET", "/image.jpg?bac=b1", entetes=COOKIE)
img = Image.open(io.BytesIO(corps)).convert("RGB")
verifier("image du direct (bac 1)", st == 200 and ent["Content-Type"] == "image/jpeg" and img.size == (960, 540)
         and abs(img.getpixel((480, 500))[0] - 90) < 12)
verifier("poisson nommé encadré en ambre, intrus en rouge",
         img.getpixel((300, 300))[0] > 180 and img.getpixel((300, 300))[2] < 140
         and img.getpixel((600, 150))[0] > 180 and img.getpixel((600, 150))[1] < 130,
         (img.getpixel((300, 300)), img.getpixel((600, 150))))
st, _, corps = req("GET", "/image.jpg?bac=b2", entetes=COOKIE)
img = Image.open(io.BytesIO(corps)).convert("RGB")
verifier("bac arrêté : image d'attente", st == 200 and img.size == (960, 540) and img.getpixel((10, 10))[2] < 40)
st, ent, corps = req("GET", "/direct.mjpg?bac=b1", entetes=COOKIE)
verifier("flux MJPEG (bac 1)", st == 200 and "multipart/x-mixed-replace" in ent["Content-Type"]
         and corps.startswith(b"--image") and b"\xff\xd8" in corps[:200])
st, ent, corps = req("GET", "/direct.mjpg?bac=b2", entetes=COOKIE)
verifier("flux MJPEG (bac arrêté) : image d'attente", st == 200 and corps.startswith(b"--image") and b"\xff\xd8" in corps[:200])
st, *_ = req("GET", "/direct.mjpg?bac=zz", entetes=COOKIE)
verifier("bac inconnu : 404", st == 404)

# --- fiches
st, _, corps = req("GET", f"/api/poisson?cle={kohaku}", entetes=COOKIE)
t = json.loads(corps)
verifier("fiche détaillée", st == 200 and t["nom"] == "Kohaku" and t["signes"].startswith("tache")
         and t["mesures"][0]["taille"] == "31,5 cm" and "Note" in t["types"] and t["album"] == [])
st, *_ = req("GET", "/api/poisson?cle=inconnu", entetes=COOKIE)
verifier("fiche inconnue : 404", st == 404)

# --- écritures
st, *_ = req("POST", "/api/mesure", json.dumps({"cle": kohaku, "cm": "33,2", "g": "1 250", "date": "2026-11-01",
                                                 "note": "épuisette"}), JSON_)
dern = r.mesures(kohaku)[-1]
verifier("mesure depuis le téléphone", st == 200 and dern["cm"] == 33.2 and dern["g"] == 1250 and dern["source"] == "main"
         and modifs, dern)
st, *_ = req("POST", "/api/mesure", json.dumps({"cle": kohaku, "cm": "0"}), JSON_)
verifier("mesure absurde refusée", st == 400)
st, *_ = req("POST", "/api/mesure", json.dumps({"cle": kohaku, "cm": "30", "date": "2999-01-01"}), JSON_)
verifier("date future refusée", st == 400)
st, *_ = req("POST", "/api/note", json.dumps({"cle": kohaku, "type": "Traitement", "texte": "Bain de sel"}), JSON_)
verifier("note au carnet", st == 200 and r.carnet(kohaku)[0]["texte"] == "Bain de sel")
st, *_ = req("POST", "/api/note", json.dumps({"cle": "inconnu", "texte": "x"}), JSON_)
verifier("poisson inconnu refusé", st == 400)
st, *_ = req("POST", "/api/note", b"[1]", JSON_)
verifier("JSON qui n'est pas un objet : 400, pas de connexion coupée", st == 400)

tampon = io.BytesIO()
Image.new("RGB", (1600, 1200), (200, 90, 40)).save(tampon, "JPEG")
st, *_ = req("POST", f"/api/photo?cle={kohaku}&nom=IMG_0042.jpg", tampon.getvalue(), {**COOKIE, "Content-Type": "image/jpeg"})
verifier("photo dans l'album", st == 200 and len(r.album(kohaku)) == 1 and "IMG_0042" in r.album(kohaku)[0].name)
st, *_ = req("POST", f"/api/photo?cle={kohaku}", b"pas une image", {**COOKIE, "Content-Type": "image/jpeg"})
verifier("fichier illisible refusé", st == 400 and len(r.album(kohaku)) == 1)
bombe = Image.new("1", (15000, 13000))         # 195 Mpx, quelques Ko en PNG
chemin_bombe = d / "bombe.png"
bombe.save(chemin_bombe)
st, *_ = req("POST", f"/api/photo?cle={anonyme}", chemin_bombe.read_bytes(), {**COOKIE, "Content-Type": "image/png"})
verifier("image géante refusée proprement", st == 400 and not r.album(anonyme), st)
st, *_ = req("POST", f"/api/photo?cle={kohaku}", b"", {**COOKIE, "Content-Length": "-1"})
verifier("taille négative refusée", st == 400)
st, _, _ = req("GET", f"/img/album/{kohaku}/{r.album(kohaku)[0].name}", entetes=COOKIE)
verifier("photo d'album servie", st == 200)
st, ent, _ = req("GET", f"/img/portrait/{kohaku}", entetes=COOKIE)
verifier("portrait servi", st == 200 and ent["Content-Type"] == "image/jpeg")

st, *_ = req("POST", "/api/lot", json.dumps({"id": lot, "genre": "comptage", "nombre": "87", "note": "à l'œil"}), JSON_)
verifier("comptage d'un lot", st == 200 and elevage.bilan(elevage.Elevage(d).lot(lot))["effectif"] == 87)
st, *_ = req("POST", "/api/lot", json.dumps({"id": lot, "genre": "pertes", "nombre": "500"}), JSON_)
verifier("pertes plus nombreuses que l'effectif refusées", st == 400)
st, *_ = req("POST", "/api/lot", json.dumps({"id": lot, "genre": "transfert", "nombre": "1"}), JSON_)
verifier("genre non permis depuis le téléphone refusé", st == 400)
st, *_ = req("POST", "/api/lot", json.dumps({"id": "l0", "genre": "comptage", "nombre": "1"}), JSON_)
verifier("lot inconnu refusé", st == 400)

# --- photos d'événement et clips
st, ent, _ = req("GET", "/img/evenement/b1/2026-11-09_06-12-00_b1_3.jpg", entetes=COOKIE)
verifier("photo d'événement servie", st == 200 and ent["Content-Type"] == "image/jpeg")
st, ent, corps = req("GET", "/clips/b1/2026-11-09_06-11-55.mp4", entetes={**COOKIE, "Range": "bytes=2-5"})
verifier("clip : plage (Range)", st == 206 and corps == b"2345" and ent["Content-Range"] == "bytes 2-5/10")
st, ent, corps = req("GET", "/clips/b1/2026-11-09_06-11-55.mp4?telecharger=1", entetes=COOKIE)
verifier("clip : téléchargement", st == 200 and corps == b"0123456789"
         and "attachment; filename*=UTF-8''2026-11-09_06-11-55.mp4" == ent.get("Content-Disposition"))
st, *_ = req("GET", "/clips/b1/2026-11-09_06-11-55.mp4", entetes={**COOKIE, "Range": "bytes=50-"})
verifier("clip : plage hors du fichier", st == 416)
pieges = ["/clips/b1/inconnu.mp4", "/clips/b2/2026-11-09_06-11-55.mp4", "/clips/b1/..%2F..%2F..%2Fsecret.txt",
          "/clips/..%2F..%2Fsecret.txt", "/img/evenement/b1/..%2F..%2F..%2Fsecret.txt",
          "/img/evenement/..%2F..%2Fpoissons/x.jpg", "/img/evenement/b1/E:x.jpg", "/img/evenement/b1/../photos/x.jpg",
          f"/img/album/{kohaku}/..%2F..%2Fregistre.json", f"/img/portrait/..%2F{kohaku}", "/img/portrait/inconnu"]
statuts = [req("GET", p, entetes=COOKIE)[0] for p in pieges]
verifier("pas de sortie du dossier, noms inconnus : 404", all(x == 404 for x in statuts), statuts)

# --- blocages
for _ in range(web.ESSAIS_MAX):
    req("GET", "/?code=00000000")
st, _, corps = req("GET", f"/?code={CODE}")
verifier("trop d'essais faux : bloqué, même avec le bon code", st == 403 and "10 minutes" in corps.decode())
st, *_ = req("GET", "/api/etat", entetes=COOKIE)
verifier("un téléphone déjà connecté continue", st == 200)
web.PAUSE_ECHEC_S = 0
s2 = web.Serveur(r, lambda: d, lambda: [], lambda bac: None, "87654321", port=0, hote="127.0.0.1")
for k in range(web.ESSAIS_MAX_TOTAL):
    s2.verifier(f"10.0.{k}.1", "00000000")
verifier("trop d'essais en changeant d'adresse : bloqué partout", not s2.verifier("10.9.9.9", "87654321"))
s2.httpd.server_close()

# --- accès coupé : une connexion gardée ouverte n'a plus rien
c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
c.request("GET", "/api/etat", headers=COOKIE)
rep = c.getresponse()
rep.read()
verifier("connexion gardée ouverte : accès au départ", rep.status == 200)
s.arreter()
try:
    c.request("GET", "/api/etat", headers=COOKIE)
    statut = c.getresponse().status
except OSError:
    statut = "coupée"
verifier("accès coupé : la connexion déjà ouverte n'a plus rien", statut in ("coupée", 403) and not s.actif, statut)
print(f"\n{ok} vérifications réussies")
