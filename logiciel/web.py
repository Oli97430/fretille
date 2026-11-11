"""
Frétille — accès depuis le téléphone, sur le Wi-Fi de la maison.

Petit serveur web lancé par l'application : direct de chaque bac, état des bacs, derniers
événements (prédateur, santé, eau), fiches des poissons, lots d'alevins, clips ; depuis le bassin
on note une mesure, une observation au carnet, un comptage, on ajoute une photo à l'album.

Protégé par un code à 8 chiffres (cookie après la première saisie ; le QR code de
l'application le contient). Écoute sur le réseau local seulement : la box ne l'ouvre pas sur
Internet. Sans dépendance : http.server de Python, page HTML intégrée. Repris de Broutille.
"""
import hashlib
import hmac
import html
import json
import re
import secrets
import socket
import tempfile
import threading
import time
from datetime import date, datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import cv2
import numpy as np
from PIL import Image, ImageDraw

import fiches
import i18n
from elevage import LIBELLES, Elevage, bilan
from fretille_cam import dossier_bac, lire_evenements
from i18n import t
from outils import nom_fichier
from registre import ALBUM

PORT = 8643                      # Broutille a 8642 : les deux peuvent tourner sur le même PC
MAX_PHOTO = 25 * 1024 * 1024
TEXTE = "text/plain; charset=utf-8"
ESSAIS_MAX, BLOCAGE_S = 8, 600       # code faux 8 fois : adresse bloquée 10 min
ESSAIS_MAX_TOTAL = 30                 # tous appareils confondus : au-delà, plus aucun code accepté 10 min
PAUSE_ECHEC_S = 1.0                   # chaque code faux coûte une seconde : essayer en masse devient inutile
GENRES_LOT = ("comptage", "pertes", "vente", "note")    # ce qu'on note depuis le téléphone
_VERROU_ELEVAGE = threading.Lock()    # deux téléphones qui notent en même temps : pas d'écriture perdue


def nouveau_code():
    # 8 chiffres : même au rythme maximal permis par les blocages, des siècles pour le deviner
    return f"{secrets.randbelow(10 ** 8):08d}"


def adresse_locale():
    """Adresse IP de ce PC sur le réseau local (celle qu'un téléphone du même Wi-Fi peut joindre)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # aucun paquet envoyé : choisit juste l'interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _police(taille, gras=False):
    """Police de la langue choisie (caractères chinois, japonais, coréens, thaïs compris)."""
    return i18n.police_pil(taille, gras)


def jour_fr(iso):
    return f"{iso[8:10]}/{iso[5:7]}" if iso else ""


def nombre_fr(v):
    """12,5 ou 12.5 selon la langue."""
    return i18n.nombre(v, 2)


def taille_lisible(cm):
    return t("{v} cm", v=nombre_fr(cm)) if cm is not None else ""


def poids_lisible(g):
    if g is None:
        return ""
    return t("{v} kg", v=nombre_fr(round(g / 1000, 2))) if g >= 1000 else t("{v} g", v=nombre_fr(g))


def _json_script(valeur):
    """JSON qu'on peut poser dans une balise <script> : ni « </script> », ni « <!-- », ni fin de ligne JS."""
    return (json.dumps(valeur, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _nombre(texte):
    """« 12,5 » ou « 1 250 » → float ; vide → None."""
    s = str(texte if texte is not None else "").replace(",", ".").replace(" ", "").replace(" ", "")
    return float(s) if s else None


HOTE_PERMIS = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|localhost|\[[0-9a-fA-F:]+\])(:\d+)?$")
NOM_IMAGE = re.compile(r"^[A-Za-z0-9_-][\w.-]*\.jpg$", re.ASCII)
NOM_ALBUM = re.compile(r"^[A-Za-z0-9_-][\w.-]*\.(jpg|jpeg|png)$", re.ASCII | re.IGNORECASE)
NOM_BAC = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class Serveur:
    """bacs() -> [{id, nom, type, etat, raisons, nb, attendus}] ; source(bac_id) -> (image BGR, etat) ou None ;
    dossier() -> dossier des données ; apres_modif() : prévient l'application qu'une fiche ou un lot
    a changé (appelé depuis un fil du serveur)."""

    def __init__(self, registre, dossier, bacs, source, code, apres_modif=lambda: None, port=PORT, hote="0.0.0.0",
                 secret=None):
        self.registre, self.dossier, self.bacs, self.source = registre, dossier, bacs, source
        self.code, self.apres_modif = code, apres_modif
        # le cookie n'est pas le code : un jeton de 256 bits tiré du code et d'un secret propre à ce PC.
        # Impossible à deviner, il change avec le code. secret : gardé dans la config pour que les
        # téléphones restent connectés d'un lancement à l'autre.
        self.jeton = hmac.new((secret or secrets.token_hex(32)).encode(), code.encode(), hashlib.sha256).hexdigest()
        self.echecs = {}                # ip -> (nombre, instant du dernier)
        self.echecs_total = []          # instants des codes faux, toutes adresses (on peut changer d'adresse)
        self._verrou_codes = threading.Lock()
        # les téléphones déjà connectés (jeton en cookie) ne passent jamais par là : un blocage ne les gêne pas
        serveur = self

        class Gestion(_Gestion):
            app = serveur

        self.actif = True
        self.connexions = set()         # sockets ouvertes : coupées net à l'arrêt (code changé, accès coupé)
        self._verrou_cnx = threading.Lock()
        self.flux = threading.BoundedSemaphore(3)       # directs MJPEG simultanés
        self.envois = threading.BoundedSemaphore(2)     # photos reçues en même temps
        self._images = {}               # bac -> (instant, dernière image encodée), partagée entre spectateurs
        self._verrou_image = threading.Lock()
        self.httpd = ThreadingHTTPServer((hote, port), Gestion)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda *_: None      # téléphone parti, connexion coupée à l'arrêt : rien à signaler
        self.port = self.httpd.server_address[1]
        self.fil = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    # polices lisibles sur un petit écran, de la langue en cours (choisie après la création du serveur)
    @property
    def f_nom(self):
        return _police(28, True)

    @property
    def f_petit(self):
        return _police(18)

    def demarrer(self):
        self.fil.start()
        return self

    def arreter(self):
        self.actif = False              # une connexion encore ouverte n'est plus autorisée
        self.httpd.shutdown()
        self.httpd.server_close()
        with self._verrou_cnx:
            ouvertes = list(self.connexions)
        for c in ouvertes:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def url(self, avec_code=True):
        base = f"http://{adresse_locale()}:{self.port}/"
        return base + (f"?code={self.code}" if avec_code else "")

    # --- bacs
    def liste_bacs(self):
        try:
            return [dict(b) for b in self.bacs() or []]
        except Exception:
            return []

    def bac_connu(self, bac_id):
        return any(b.get("id") == bac_id for b in self.liste_bacs())

    def _source(self, bac_id):
        try:
            return self.source(bac_id)
        except Exception:               # caméra qui décroche : image d'attente plutôt qu'une erreur
            return None

    # --- images
    def image_direct(self, bac_id, largeur=960):
        """JPEG de l'image en cours d'un bac, avec les poissons et les intrus encadrés ; None si le bac
        est arrêté. Encodée une fois pour tous les spectateurs (au plus ~6 fois par seconde et par bac)."""
        with self._verrou_image:
            instant, jpeg = self._images.get(bac_id, (0.0, None))
            if jpeg is not None and time.time() - instant < 0.15:
                return jpeg
            jpeg = self._encoder_direct(bac_id, largeur)
            if len(self._images) > 64:              # pas de mémoire qui gonfle avec des bacs disparus
                self._images.clear()
            self._images[bac_id] = (time.time(), jpeg)
            return jpeg

    def _encoder_direct(self, bac_id, largeur):
        donnees = self._source(bac_id)
        if not donnees:
            return None
        image, etat = donnees
        etat = etat or {}
        h, w = image.shape[:2]
        e = min(1.0, largeur / w)
        petite = cv2.resize(image, (int(w * e), int(h * e)), interpolation=cv2.INTER_AREA) if e < 1 else image
        img = Image.fromarray(cv2.cvtColor(petite, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(img, "RGBA")
        fiches_ = self.registre.fiches
        nommes = []
        for _num, boite, texte, sure, *reste in etat.get("pistes") or []:
            if not sure:
                continue
            cle = reste[0] if reste else None
            x, y, bw, bh = (int(v * e) for v in boite)
            texte = texte or ""
            if cle in fiches_ and texte and not texte.endswith("?"):
                nommes.append((x, y, bw, bh, fiches_[cle]["nom"]))
            else:                       # poisson anonyme : trait fin, pour ne pas charger l'image
                d.rectangle([x, y, x + bw, y + bh], outline=(60, 198, 192, 200), width=1)
        for x, y, bw, bh, nom in nommes:                 # par-dessus les anonymes
            d.rounded_rectangle([x, y, x + bw, y + bh], radius=8, outline=(240, 180, 76, 255), width=3)
            self._etiquette(d, x, y, nom, (240, 180, 76))
        for boite, texte, *reste in etat.get("intrus") or []:
            danger = bool(reste[0]) if reste else True
            x, y, bw, bh = (int(v * e) for v in boite)
            d.rectangle([x, y, x + bw, y + bh], outline=(248, 81, 73, 255), width=4 if danger else 2)
            nom = t(str(texte)) if texte else t("Intrus")        # espèce du détecteur, en français : traduite ici
            self._etiquette(d, x, y, t("{nom} !", nom=nom) if danger else nom, (248, 81, 73))
        ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 75])
        return jpeg.tobytes() if ok else None

    def _etiquette(self, d, x, y, texte, couleur):
        tw = d.textlength(texte, font=self.f_nom)
        yt = max(0, y - 42)
        d.rounded_rectangle([x, yt, x + tw + 20, yt + 38], radius=10, fill=(11, 20, 24, 215))
        d.text((x + 10, yt + 3), texte, font=self.f_nom, fill=couleur)

    def image_attente(self, bac_id=""):
        img = Image.new("RGB", (960, 540), (11, 20, 24))
        d = ImageDraw.Draw(img)
        d.text((480, 255), t("Bac arrêté"), font=self.f_nom, fill=(138, 165, 173), anchor="mm")
        nom = next((b.get("nom", "") for b in self.liste_bacs() if b.get("id") == bac_id), "")
        if nom:
            d.text((480, 300), str(nom), font=self.f_petit, fill=(138, 165, 173), anchor="mm")
        ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR))
        return jpeg.tobytes()

    # --- données
    def _evenements(self):
        try:
            return lire_evenements(self.dossier())
        except (OSError, ValueError):
            return []

    def _lots(self):
        try:
            return Elevage(self.dossier()).lots
        except (OSError, ValueError):
            return []

    def etat(self):
        bacs = self.liste_bacs()
        noms_bacs = {b.get("id"): b.get("nom", b.get("id")) for b in bacs}
        fiches_ = self.registre.copie_fiches()
        evenements = []
        for l in reversed(self._evenements()[-30:]):
            bac = l.get("bac", "")
            evenements.append({
                "id": l.get("id", ""), "date": jour_fr(l.get("date", "")), "heure": l.get("heure", "")[:5],
                "bac": bac, "nom_bac": noms_bacs.get(bac, bac), "genre": l.get("genre", ""),
                # titre enregistré en français : traduit à l'affichage s'il est au catalogue
                "titre": t(l.get("titre", "")) if l.get("titre") else "", "detail": l.get("detail", ""),
                "photo": Path(l["photo"]).name if l.get("photo") else "",
                "video": Path(l["video"]).name if l.get("video") else "",
                "cle": l.get("cle", ""), "nom": fiches_.get(l.get("cle", ""), {}).get("nom", "")})
        poissons = []
        for cle, f in sorted(fiches_.items(), key=lambda x: (x[1].get("provisoire", False), x[1].get("nom", ""))):
            mesures = f.get("mesures", [])
            cm = next((m["cm"] for m in reversed(mesures) if m.get("cm") is not None), None)
            g = next((m["g"] for m in reversed(mesures) if m.get("g") is not None), None)
            poissons.append({"cle": cle, "nom": f.get("nom", ""), "identite": fiches.resume_identite(f),
                             "bac": f.get("bac", ""), "nom_bac": noms_bacs.get(f.get("bac", ""), f.get("bac", "")),
                             "taille": taille_lisible(cm), "poids": poids_lisible(g)})
        lots = []
        for lot in self._lots():
            if lot.get("clos"):
                continue
            try:
                b = bilan(lot)
            except (KeyError, ValueError, TypeError):
                continue
            espece = lot.get("espece", "")
            lots.append({"id": lot["id"], "nom": lot.get("nom", ""), "espece": t(espece) if espece else "",
                         "bac": lot.get("bac", ""), "nom_bac": noms_bacs.get(lot.get("bac", ""), lot.get("bac", "")),
                         "effectif": b["effectif"], "age_j": b["age_j"], "survie": b["survie"]})
        return {"heure": datetime.now().strftime("%H:%M"), "bacs": bacs, "evenements": evenements,
                "poissons": poissons, "lots": lots,
                "clips": [f"bacs/{bac}/clips/{p.name}" for bac, p in self.clips()[:12]],
                "genres_lot": {g: t(LIBELLES[g]) for g in GENRES_LOT}}

    def clips_bac(self, bac_id):
        """Clips d'un bac, du plus récent au plus ancien."""
        if not NOM_BAC.match(bac_id or ""):
            return []
        d = dossier_bac(self.dossier(), bac_id) / "clips"
        liste = []
        for p in d.glob("*.mp4") if d.is_dir() else []:
            try:
                liste.append((p.stat().st_mtime, p))
            except OSError:             # effacé par le ménage entre-temps
                pass
        return [p for _t, p in sorted(liste, reverse=True)]

    def clips(self):
        """[(bac, chemin)] de tous les bacs, du plus récent au plus ancien."""
        racine = Path(self.dossier()) / "bacs"
        liste = []
        for d in racine.iterdir() if racine.is_dir() else []:
            if not NOM_BAC.match(d.name):
                continue
            for p in self.clips_bac(d.name):
                try:
                    liste.append((p.stat().st_mtime, d.name, p))
                except OSError:
                    pass
        return [(bac, p) for _t, bac, p in sorted(liste, key=lambda x: x[0], reverse=True)]

    def poisson(self, cle):
        f = self.registre.copie_fiches().get(cle)
        if not f:
            return None
        mesures = self.registre.mesures(cle)
        espece = f.get("espece", "")
        return {"cle": cle, "nom": f.get("nom", ""), "espece": t(espece) if espece else "",
                "identite": fiches.resume_identite(f), "signes": f.get("signes", ""), "notes": f.get("notes", ""),
                "bac": f.get("bac", ""),
                "mesures": [{"date": jour_fr(m["date"]) + "/" + m["date"][:4], "taille": taille_lisible(m.get("cm")),
                             "poids": poids_lisible(m.get("g")), "source": m.get("source", ""), "note": m.get("note", "")}
                            for m in reversed(mesures[-12:])],
                "carnet": [{"date": jour_fr(n["date"]) + "/" + n["date"][:4],
                            "type": t(n["type"]) if n.get("type") else "", "texte": n["texte"]}
                           for n in reversed(self.registre.carnet(cle)[-20:])],
                "album": [p.name for p in self.registre.album(cle)[:24]],
                # types enregistrés en français (valeur envoyée) et leur nom affiché
                "types": list(fiches.TYPES_CARNET), "noms_types": {x: t(x) for x in fiches.TYPES_CARNET}}

    # --- sécurité
    def bloque(self, ip):
        with self._verrou_codes:
            maintenant = time.time()
            self.echecs_total = [x for x in self.echecs_total if maintenant - x < BLOCAGE_S]
            if len(self.echecs_total) >= ESSAIS_MAX_TOTAL:
                return True
            n, dernier = self.echecs.get(ip, (0, 0))
            if n >= ESSAIS_MAX and maintenant - dernier < BLOCAGE_S:
                return True
            if n >= ESSAIS_MAX or (n and maintenant - dernier >= BLOCAGE_S):
                self.echecs.pop(ip, None)
            return False

    def verifier(self, ip, code):
        if self.bloque(ip):
            return False
        if code and hmac.compare_digest(str(code).encode(), self.code.encode()):
            with self._verrou_codes:
                self.echecs.pop(ip, None)
            return True
        if code:
            with self._verrou_codes:
                n, _t = self.echecs.get(ip, (0, 0))
                self.echecs[ip] = (n + 1, time.time())
                self.echecs_total.append(time.time())
                if len(self.echecs) > 1000:     # pas de mémoire qui gonfle avec des adresses inventées
                    self.echecs.clear()
            time.sleep(PAUSE_ECHEC_S)
        return False


class _Gestion(BaseHTTPRequestHandler):
    app = None
    protocol_version = "HTTP/1.1"
    timeout = 20                        # une connexion muette ne garde pas un fil pour toujours

    def setup(self):
        super().setup()
        with self.app._verrou_cnx:
            self.app.connexions.add(self.connection)

    def finish(self):
        with self.app._verrou_cnx:
            self.app.connexions.discard(self.connection)
        try:
            super().finish()
        except OSError:
            pass

    def _hote_ok(self):
        """Adresse IP ou localhost seulement : une page web piégée qui ferait pointer son nom de
        domaine vers ce PC (« DNS rebinding ») est refusée."""
        if HOTE_PERMIS.match(self.headers.get("Host", "")):
            return True
        self._envoyer(421, t("hôte refusé"), TEXTE)
        self.close_connection = True
        return False

    def _proteger(self, traitement):
        if not self._hote_ok():
            return
        try:
            traitement()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            self.close_connection = True
        except Exception:               # jamais de connexion coupée sans réponse
            self.close_connection = True
            try:
                self._envoyer(500, t("erreur"), TEXTE)
            except OSError:
                pass

    def log_message(self, *_):      # pas de bavardage dans la console
        pass

    # --- outils
    def _envoyer(self, statut, corps, type_="text/html; charset=utf-8", entetes=()):
        corps = corps.encode("utf-8") if isinstance(corps, str) else corps
        self.send_response(statut)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in entetes:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corps)

    def _json(self, donnees, statut=200):
        self._envoyer(statut, json.dumps(donnees, ensure_ascii=False), "application/json; charset=utf-8")

    def _cookie(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c["fretille"].value if "fretille" in c else ""

    def _autorise(self):
        return self.app.actif and hmac.compare_digest(self._cookie().encode(), self.app.jeton.encode())

    def _poser_jeton(self):
        self._envoyer(303, "", entetes=[("Location", "/"), ("Set-Cookie",
                      f"fretille={self.app.jeton}; Max-Age=31536000; HttpOnly; SameSite=Strict; Path=/")])

    def _lire_corps(self, maxi):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError(t("taille illisible")) from None
        if not 0 <= n <= maxi:          # négatif : read(-1) lirait jusqu'à la fin, sans limite
            self.close_connection = True    # corps non lu : la connexion ne peut pas resservir
            raise ValueError(t("taille refusée"))
        return self.rfile.read(n)

    def _fichier_image(self, chemin):
        try:
            with Image.open(chemin) as img:
                img.draft("RGB", (900, 900))
                img = img.convert("RGB")
                img.thumbnail((900, 900))
                tampon = tempfile.SpooledTemporaryFile()
                img.save(tampon, "JPEG", quality=82)
                tampon.seek(0)
                self._envoyer(200, tampon.read(), "image/jpeg")
        except (OSError, ValueError, Image.DecompressionBombError):
            self._envoyer(404, t("introuvable"), TEXTE)

    def _bac_demande(self, q):
        """Bac de la requête (?bac=) s'il existe ; sans précision, le premier."""
        bacs = self.app.liste_bacs()
        demande = q.get("bac", [""])[0]
        if not demande:
            return bacs[0]["id"] if bacs else None
        return demande if any(b.get("id") == demande for b in bacs) else None

    # --- GET
    def do_GET(self):
        self._proteger(self._get)

    def _get(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        ip = self.client_address[0]
        if "code" in q:                     # lien du QR code : on pose le cookie et on nettoie l'adresse
            if self.app.verifier(ip, q["code"][0]):
                self._poser_jeton()
            else:
                self._envoyer(403, page_code(t("Code faux.") if not self.app.bloque(ip)
                                             else t("Trop d'essais : réessayez dans 10 minutes.")))
            return
        if not self._autorise():
            self._envoyer(200 if url.path == "/" else 403, page_code())
            return
        self._route_get(unquote(url.path), q)

    def _route_get(self, chemin, q):
        a = self.app
        morceaux = chemin.split("/")
        if chemin == "/":
            self._envoyer(200, page())
        elif chemin == "/api/etat":
            self._json(a.etat())
        elif chemin == "/api/poisson":
            p = a.poisson(q.get("cle", [""])[0])
            self._json(p, 200) if p else self._json({"erreur": t("inconnu")}, 404)
        elif chemin in ("/image.jpg", "/direct.mjpg"):
            bac = self._bac_demande(q)
            if bac is None:
                self._envoyer(404, t("bac inconnu"), TEXTE)
            elif chemin == "/image.jpg":
                self._envoyer(200, a.image_direct(bac) or a.image_attente(bac), "image/jpeg")
            else:
                self._mjpeg(bac)
        elif chemin.startswith("/img/evenement/"):
            # /img/evenement/<bac>/<photo>.jpg : un nom de photo, rien d'autre (pas de « E:x.jpg » ni de chemin)
            if len(morceaux) == 5 and NOM_BAC.match(morceaux[3]) and NOM_IMAGE.match(morceaux[4]):
                self._fichier_image(dossier_bac(a.dossier(), morceaux[3]) / "photos" / morceaux[4])
            else:
                self._envoyer(404, "", "text/plain")
        elif chemin.startswith("/img/portrait/"):
            cle = morceaux[3] if len(morceaux) == 4 else ""
            p = a.registre.photo_profil(cle) if cle in a.registre.fiches else None
            self._fichier_image(p) if p else self._envoyer(404, "", "text/plain")
        elif chemin.startswith("/img/album/"):
            if len(morceaux) == 5 and morceaux[3] in a.registre.fiches and NOM_ALBUM.match(morceaux[4]):
                self._fichier_image(a.registre.dossier / morceaux[3] / ALBUM / morceaux[4])
            else:
                self._envoyer(404, "", "text/plain")
        elif chemin.startswith("/clips/"):
            fichier = None
            if len(morceaux) == 4 and NOM_BAC.match(morceaux[2]):
                # seulement un clip présent dans le dossier de ce bac
                fichier = next((p for p in a.clips_bac(morceaux[2]) if p.name == morceaux[3]), None)
            if fichier:
                self._envoyer_video(fichier, telecharger="telecharger" in q)
            else:
                self._envoyer(404, "", "text/plain")
        else:
            self._envoyer(404, t("introuvable"), TEXTE)

    def _envoyer_video(self, chemin, telecharger=False):
        """MP4 avec prise en charge des plages (Range) : le lecteur vidéo de l'iPhone l'exige."""
        taille = chemin.stat().st_size
        debut, fin = 0, taille - 1
        plage = self.headers.get("Range", "")
        if plage.startswith("bytes="):
            a, _, b = plage[6:].split(",")[0].partition("-")
            try:
                if a:
                    debut, fin = int(a), (int(b) if b else taille - 1)
                elif b:
                    debut = max(0, taille - int(b))
            except ValueError:
                debut, fin = 0, taille - 1
            if debut > fin or debut >= taille:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{taille}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            fin = min(fin, taille - 1)
        self.send_response(206 if plage else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fin - debut + 1))
        if plage:
            self.send_header("Content-Range", f"bytes {debut}-{fin}/{taille}")
        if telecharger:                 # nom accentué : forme RFC 5987, l'en-tête reste en ASCII
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(chemin.name)}")
        self.end_headers()
        with open(chemin, "rb") as f:
            f.seek(debut)
            reste = fin - debut + 1
            while reste > 0:
                bloc = f.read(min(256 * 1024, reste))
                if not bloc:
                    break
                self.wfile.write(bloc)
                reste -= len(bloc)

    def _mjpeg(self, bac):
        """Direct en MJPEG, ~5 images/s : lu tel quel par la balise <img> du navigateur du téléphone."""
        if not self.app.flux.acquire(blocking=False):
            self._envoyer(503, t("trop de directs ouverts"), TEXTE)
            return
        try:
            self._flux_mjpeg(bac)
        finally:
            self.app.flux.release()

    def _flux_mjpeg(self, bac):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=image")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        fin = time.time() + 3600             # une heure max par connexion : la page se reconnecte seule
        while time.time() < fin and self.app.actif:
            jpeg = self.app.image_direct(bac)
            en_direct = jpeg is not None
            jpeg = jpeg or self.app.image_attente(bac)
            self.wfile.write(b"--image\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                             + b"\r\n\r\n" + jpeg + b"\r\n")
            self.wfile.flush()
            time.sleep(0.2 if en_direct else 2.0)

    # --- POST
    def do_POST(self):
        self._proteger(self._post)

    def _post(self):
        url = urlparse(self.path)
        ip = self.client_address[0]
        if url.path == "/connexion":
            try:
                code = parse_qs(self._lire_corps(1000).decode("utf-8", "replace")).get("code", [""])[0].strip()
            except ValueError:
                code = ""
            if self.app.verifier(ip, code):
                self._poser_jeton()
            else:
                msg = t("Trop d'essais : réessayez dans 10 minutes.") if self.app.bloque(ip) else t("Code faux.")
                self._envoyer(403, page_code(msg))
            return
        if not self._autorise():
            self._json({"erreur": "code"}, 403)
            return
        q = parse_qs(url.query)
        a = self.app
        try:
            if url.path == "/api/photo":
                cle = q.get("cle", [""])[0]
                if cle not in a.registre.fiches:
                    raise ValueError(t("poisson inconnu"))
                if not a.envois.acquire(timeout=30):
                    self.close_connection = True
                    raise ValueError(t("occupé : réessayez"))
                try:
                    corps = self._lire_corps(MAX_PHOTO)
                    nom = nom_fichier(Path(q.get("nom", ["telephone"])[0]).stem)[:30] or "telephone"
                    with tempfile.TemporaryDirectory() as d:
                        tmp = Path(d) / f"{nom}.jpg"
                        tmp.write_bytes(corps)
                        del corps
                        if not a.registre.ajouter_album(cle, [tmp]):
                            raise ValueError(t("photo illisible"))
                finally:
                    a.envois.release()
            elif url.path in ("/api/mesure", "/api/note", "/api/lot"):
                corps = self._lire_corps(20000)
                try:
                    donnees = json.loads(corps or b"{}")
                except ValueError:
                    donnees = None
                if not isinstance(donnees, dict):
                    raise ValueError(t("données illisibles"))
                try:
                    jour = date.fromisoformat(str(donnees.get("date") or date.today().isoformat()))
                except ValueError:
                    raise ValueError(t("date illisible")) from None
                if jour > date.today():
                    raise ValueError(t("date dans le futur"))
                if url.path == "/api/lot":
                    self._noter_lot(donnees, jour)
                else:
                    cle = donnees.get("cle", "")
                    if not isinstance(cle, str) or cle not in a.registre.fiches:
                        raise ValueError(t("poisson inconnu"))
                    if url.path == "/api/mesure":
                        a.registre.ajouter_mesure(cle, jour.isoformat(), cm=_nombre(donnees.get("cm")),
                                                  grammes=_nombre(donnees.get("g")), source="main",
                                                  note=str(donnees.get("note", ""))[:200])
                    else:
                        a.registre.ajouter_note(cle, jour.isoformat(), str(donnees.get("type") or "Note")[:30],
                                                str(donnees.get("texte", ""))[:1000])
            else:
                self._json({"erreur": t("introuvable")}, 404)
                return
        except (ValueError, TypeError, KeyError) as e:
            self._json({"erreur": str(e) if not isinstance(e, KeyError) else t("inconnu")}, 400)
            return
        a.apres_modif()
        self._json({"ok": True})

    def _noter_lot(self, donnees, jour):
        genre = donnees.get("genre", "")
        if genre not in GENRES_LOT:
            raise ValueError(t("genre refusé"))
        nombre = donnees.get("nombre")
        if nombre not in (None, ""):
            try:
                nombre = int(str(nombre).replace(" ", ""))
            except ValueError:
                raise ValueError(t("nombre impossible")) from None
            if not 0 <= nombre <= 1_000_000:
                raise ValueError(t("nombre impossible"))
        with _VERROU_ELEVAGE:
            elevage = Elevage(self.app.dossier())
            ident = str(donnees.get("id", ""))
            lot = elevage.lot(ident)
            if lot is None or lot.get("clos"):
                raise ValueError(t("lot inconnu"))
            elevage.ajouter(ident, jour.isoformat(), genre, nombre=None if nombre == "" else nombre,
                            note=str(donnees.get("note", ""))[:200])


PAGE_CODE = """<!doctype html><html lang="{langue}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Frétille</title>
<style>body{margin:0;background:#0b1418;color:#e6f1f3;font:17px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}
form{background:#13222a;padding:28px;border-radius:16px;width:min(320px,86vw)}h1{margin:0 0 6px}p{color:#8aa5ad;margin:0 0 18px}
input{width:100%;box-sizing:border-box;font-size:28px;letter-spacing:6px;text-align:center;padding:12px;border-radius:10px;border:1px solid #1f3540;background:#0b1418;color:#e6f1f3}
button{margin-top:14px;width:100%;padding:14px;font-size:17px;font-weight:600;border:0;border-radius:10px;background:#3cc6c0;color:#062022}
.e{color:#f85149;margin-top:12px;min-height:1em}</style></head><body>
<form method="post" action="/connexion"><h1>🐟 Frétille</h1><p>{consigne}</p>
<input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" autofocus>
<button>{entrer}</button><div class="e">{erreur}</div></form></body></html>"""


def page_code(erreur=""):
    """Page du code, dans la langue choisie ; chaque texte échappé (un catalogue ne peut pas glisser de HTML)."""
    valeurs = {"langue": i18n.langue, "consigne": t("Code affiché dans l'application, bouton 📱 Téléphone."),
               "entrer": t("Entrer"), "erreur": erreur}
    # un seul passage : un texte traduit qui contiendrait « {erreur} » n'est pas remplacé à son tour
    return re.sub(r"\{(langue|consigne|entrer|erreur)\}", lambda m: html.escape(valeurs[m.group(1)]), PAGE_CODE)


# Page du téléphone. Chaque texte affiché y est écrit T("texte français exact") : le serveur y pose à chaque
# demande le dictionnaire TR des traductions (à la place de /*TRADUCTIONS*/), la langue et la virgule.
PAGE = r"""<!doctype html><html lang="__LANGUE__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1418"><title>Frétille</title>
<style>
:root{--fond:#0b1418;--carte:#13222a;--bord:#1f3540;--texte:#e6f1f3;--discret:#8aa5ad;--accent:#3cc6c0;--rouge:#f85149;--ambre:#f0b44c;--vert:#6fd08c}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);font:16px system-ui,-apple-system,sans-serif;padding:0 14px 40px}
a{color:var(--accent)}
header{display:flex;align-items:center;justify-content:space-between;padding:14px 0 10px;position:sticky;top:0;background:var(--fond);z-index:2}
h1{font-size:22px;margin:0}.pastille{font-size:14px;font-weight:600;padding:6px 12px;border-radius:20px;background:var(--carte);color:var(--discret)}
.c-ok{color:var(--vert)!important}.c-surveiller{color:var(--ambre)!important}.c-alerte{color:var(--rouge)!important}.c-inconnu,.c-arrete{color:var(--discret)!important}
.puces{display:flex;gap:8px;overflow-x:auto;padding:4px 0 10px}.puce{flex:none;font:600 14px system-ui;padding:8px 14px;border-radius:20px;border:1px solid var(--bord);background:var(--carte)}
.puce.choisie{border-color:var(--accent);background:#173238}
.video{width:100%;border-radius:12px;background:#000;display:block;aspect-ratio:16/9;object-fit:contain}
section{background:var(--carte);border-radius:14px;padding:14px;margin-top:14px}h2{font-size:13px;letter-spacing:.06em;color:var(--discret);margin:0 0 10px;text-transform:uppercase}
.ligne{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid var(--bord)}.ligne:last-child{border:0}
.discret{color:var(--discret)}small{font-size:13px}
.bac{padding:8px 0;border-bottom:1px solid var(--bord)}.bac:last-child{border:0}.bac ul{margin:4px 0 0;padding-left:18px;color:var(--discret);font-size:14px}
.ev{display:flex;gap:12px;align-items:center;padding:8px 0;border-bottom:1px solid var(--bord)}.ev:last-child{border:0}
.ev img{width:110px;height:74px;object-fit:cover;border-radius:8px;background:#000;display:block}.ev.pred b{color:var(--rouge)}
.ev.sante b{color:var(--ambre)}
.poissons{display:grid;grid-template-columns:1fr 1fr;gap:10px}.t{background:var(--fond);border-radius:12px;overflow:hidden;border:1px solid var(--bord);text-align:left;color:inherit;font:inherit;padding:0}
.t img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block;background:#000}.t div{padding:8px 10px}.t b{display:block}.t small{color:var(--discret)}
dialog{border:0;border-radius:16px;background:var(--carte);color:var(--texte);width:min(560px,94vw);max-height:90vh;padding:16px}dialog::backdrop{background:#000a}
dialog section{background:var(--fond)}
label{display:block;font-size:13px;color:var(--discret);margin:10px 0 4px}input,select,textarea{width:100%;font:inherit;padding:10px;border-radius:10px;border:1px solid var(--bord);background:var(--fond);color:var(--texte)}
.rang{display:flex;gap:8px}.rang>*{flex:1;min-width:0}button.b,label.b{display:block;margin-top:10px;width:100%;padding:12px;font:600 16px system-ui;border:0;border-radius:10px;background:var(--accent);color:#062022;text-align:center}
button.sec{background:var(--bord);color:var(--texte)}.album{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.album img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px}
.lot{padding:10px 0;border-bottom:1px solid var(--bord)}.lot:last-child{border:0}.lot .rang{margin-top:6px}.lot button.b{margin-top:0;padding:10px}
.msg{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);background:var(--accent);color:#062022;padding:10px 18px;border-radius:20px;font-weight:600;display:none;z-index:9}
.msg.err{background:var(--rouge);color:#fff}
.clip{display:flex;gap:12px;padding:8px 0;border-bottom:1px solid var(--bord)}.clip:last-child{border:0}.clip video{width:150px;aspect-ratio:16/9;border-radius:8px;background:#000;object-fit:cover}
.clip a.b{display:inline-block;margin-top:8px;padding:8px 14px;border-radius:10px;background:var(--accent);color:#062022;font-weight:600;text-decoration:none}
</style></head><body>
<header><h1>🐟 Frétille</h1><span id="pastille" class="pastille">…</span></header>
<div id="puces" class="puces"></div>
<img class="video" id="video" alt="">
<section><h2 id="t-bacs"></h2><div id="bacs" class="discret">…</div></section>
<section><h2 id="t-evenements"></h2><div id="evenements" class="discret">…</div></section>
<section><h2 id="t-poissons"></h2><div id="poissons" class="poissons"></div></section>
<section><h2 id="t-lots"></h2><div id="lots" class="discret">…</div></section>
<section><h2 id="t-clips"></h2><div id="clips" class="discret">…</div></section>
<dialog id="fiche"></dialog><div id="msg" class="msg"></div>
<script>
/*TRADUCTIONS*/
function T(s,v){const r=TR[s]||s;return v?r.replace(/\{(\w+)\}/g,(m,k)=>k in v?String(v[k]):m):r}
const PL=n=>LANGUE==="fr"?Math.abs(n)<2:n===1;
const pct=x=>Math.round(x*100)+(LANGUE==="fr"?" %":"%");
const $=s=>document.querySelector(s), esc=t=>String(t??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const enc=t=>encodeURIComponent(String(t??""));
const aujourdhui=()=>new Date(Date.now()-new Date().getTimezoneOffset()*6e4).toISOString().slice(0,10);
$("#video").alt=T("Direct du bac");
$("#t-bacs").textContent=T("État des bacs");$("#t-evenements").textContent=T("Derniers événements");
$("#t-poissons").textContent=T("Mes poissons");$("#t-lots").textContent=T("Élevage");$("#t-clips").textContent="🎬 "+T("Clips");
const ETATS={ok:T("Tout va bien"),surveiller:T("À surveiller"),alerte:T("Alerte"),inconnu:T("Inconnu"),arrete:T("Arrêté")};
const ORDRE=["alerte","surveiller","inconnu","ok","arrete"];
const etatSur=e=>ETATS[e]?e:"inconnu";
let bacChoisi=null, nomsBacs={}, genresLot={}, signatures={};
function message(t,err){const m=$("#msg");m.textContent=t;m.className="msg"+(err?" err":"");m.style.display="block";setTimeout(()=>m.style.display="none",2500)}
async function api(u,o){const r=await fetch(u,o);if(r.status===403){location.reload();throw 0}const j=await r.json();if(!r.ok)throw new Error(j.erreur||T("Erreur"));return j}
function changer(cle,valeur){const s=JSON.stringify(valeur);if(signatures[cle]===s)return false;signatures[cle]=s;return true}
function cacherSiErreur(conteneur){conteneur.querySelectorAll("img").forEach(i=>i.addEventListener("error",()=>{i.style.visibility="hidden"},{once:true}))}
function choisir(id){bacChoisi=id;$("#video").src="/direct.mjpg?bac="+enc(id)+"&t="+Date.now();
 document.querySelectorAll(".puce").forEach(p=>p.classList.toggle("choisie",p.dataset.bac===id))}
function nbPoissons(b){if(b.nb==null)return "";
 if(b.attendus==null)return PL(b.nb)?T("{n} poisson",{n:b.nb}):T("{n} poissons",{n:b.nb});
 return PL(b.attendus)?T("{n} / {attendus} poisson",{n:b.nb,attendus:b.attendus}):T("{n} / {attendus} poissons",{n:b.nb,attendus:b.attendus})}
function afficherBacs(bacs){
 nomsBacs={};bacs.forEach(b=>nomsBacs[b.id]=b.nom||b.id);
 const pire=bacs.map(b=>etatSur(b.etat)).filter(e=>e!=="arrete").sort((a,b)=>ORDRE.indexOf(a)-ORDRE.indexOf(b))[0]||(bacs.length?"arrete":"inconnu");
 const p=$("#pastille");p.textContent="● "+ETATS[pire];p.className="pastille c-"+pire;
 if(changer("puces",bacs.map(b=>[b.id,b.nom,b.etat]))){
  $("#puces").innerHTML=bacs.map(b=>`<button class="puce c-${etatSur(b.etat)}" data-bac="${esc(b.id)}">${esc(b.nom||b.id)}</button>`).join("");
  if(!bacs.some(b=>b.id===bacChoisi)){const b=bacs.find(b=>b.etat!=="arrete")||bacs[0];if(b)choisir(b.id)}
  else document.querySelectorAll(".puce").forEach(p=>p.classList.toggle("choisie",p.dataset.bac===bacChoisi))}
 $("#bacs").innerHTML=bacs.length?bacs.map(b=>{const e=etatSur(b.etat);const nb=nbPoissons(b);
  return `<div class="bac"><div class="ligne" style="border:0;padding:0"><b>${esc(b.nom||b.id)}</b><span class="c-${e}">${esc(ETATS[e])}</span></div>${nb?`<small class="discret">${esc(nb)}</small>`:""}${(b.raisons||[]).length?`<ul>${b.raisons.map(r=>`<li>${esc(r)}</li>`).join("")}</ul>`:""}</div>`}).join(""):esc(T("Aucun bac"));}
function afficherEvenements(liste){if(!changer("ev",liste))return;const c=$("#evenements");
 c.innerHTML=liste.length?liste.map(v=>{const pred=v.genre==="predateur";const src=v.photo?`/img/evenement/${enc(v.bac)}/${enc(v.photo)}`:"";
  const classe=pred?"pred":v.genre==="sante"?"sante":"";
  return `<div class="ev ${classe}">${src&&pred?`<a href="${src}" target="_blank" rel="noopener"><img src="${src}" loading="lazy" alt="${esc(T("Photo"))}"></a>`:""}<div><b>${esc(v.titre)}</b><br><span class="discret">${esc(T("{date} à {heure}",{date:v.date,heure:v.heure}))} · ${esc(v.nom_bac)}${v.nom?" · "+esc(v.nom):""}</span>${v.detail?`<br><small>${esc(v.detail)}</small>`:""}<br>${src&&!pred?`<a href="${src}" target="_blank" rel="noopener">📷 ${esc(T("photo"))}</a> `:""}${v.video?`<a href="/clips/${enc(v.bac)}/${enc(v.video)}" target="_blank" rel="noopener">🎬 ${esc(T("clip"))}</a>`:""}</div></div>`}).join(""):esc(T("Aucun événement"));
 cacherSiErreur(c)}
function afficherPoissons(liste){if(!changer("poissons",liste))return;const c=$("#poissons");
 c.innerHTML=liste.length?liste.map(p=>`<button class="t" data-cle="${esc(p.cle)}"><img loading="lazy" src="/img/portrait/${enc(p.cle)}" alt=""><div><b>${esc(p.nom)}</b><small>${esc([p.taille,p.poids,p.nom_bac].filter(x=>x).join(" · "))}</small></div></button>`).join(""):`<span class="discret">${esc(T("Aucun poisson nommé"))}</span>`;
 cacherSiErreur(c)}
function afficherLots(lots){if(!changer("lots",lots))return;
 const options=Object.entries(genresLot).map(([g,l])=>`<option value="${esc(g)}">${esc(l)}</option>`).join("");
 $("#lots").innerHTML=lots.length?lots.map(l=>`<div class="lot" data-lot="${esc(l.id)}"><div class="ligne" style="border:0;padding:0"><b>${esc(l.nom)}</b><span>${esc(PL(l.effectif)?T("{n} alevin",{n:l.effectif}):T("{n} alevins",{n:l.effectif}))}</span></div>
  <small class="discret">${esc(l.espece)}${l.nom_bac?" · "+esc(l.nom_bac):""} · ${esc(T("{n} j",{n:l.age_j}))}${l.survie==null?"":" · "+esc(T("survie {p}",{p:pct(l.survie)}))}</small>
  <div class="rang"><select class="lg">${options}</select><input class="ln" inputmode="numeric" placeholder="${esc(T("Nombre"))}"><button class="b" data-action="lot">${esc(T("Noter"))}</button></div></div>`).join(""):esc(T("Aucun lot en cours"));}
function afficherClips(liste){if(!changer("clips",liste))return;
 $("#clips").innerHTML=liste.length?liste.map(chemin=>{const m=String(chemin).split("/");const bac=m[1]||"",n=m[3]||"";const url=`/clips/${enc(bac)}/${enc(n)}`;
  return `<div class="clip"><video controls playsinline preload="metadata" src="${url}#t=0.5"></video><div><b>${esc(nomsBacs[bac]||bac)}</b><br><span class="discret">${esc(T("{date} à {heure}",{date:n.slice(8,10)+"/"+n.slice(5,7),heure:n.slice(11,16).replace("-",":")}))}</span><br><a class="b" href="${url}?telecharger=1" download>⬇ ${esc(T("Télécharger"))}</a></div></div>`}).join(""):esc(T("Pas encore de clip."));}
async function etat(){try{const e=await api("/api/etat");genresLot=e.genres_lot||{};
 afficherBacs(e.bacs||[]);afficherEvenements(e.evenements||[]);afficherPoissons(e.poissons||[]);afficherLots(e.lots||[]);afficherClips(e.clips||[]);
}catch(e){}}
async function fiche(cle){const t=await api("/api/poisson?cle="+enc(cle));const f=$("#fiche");f.dataset.cle=cle;const noms=t.noms_types||{};
 f.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:start;gap:10px"><div><h1>${esc(t.nom)}</h1><div style="color:var(--ambre)">${esc(t.identite)}</div>${t.signes?`<div class="discret">${esc(t.signes)}</div>`:""}${t.notes?`<div class="discret">${esc(t.notes)}</div>`:""}</div><button class="b sec" id="fermer" aria-label="${esc(T("Fermer"))}" style="width:auto;margin:0;padding:8px 14px">✕</button></div>
 <section><h2>📏 ${esc(T("Mesure"))}</h2><div class="rang"><div><label>${esc(T("Longueur (cm)"))}</label><input id="mc" inputmode="decimal" placeholder="${esc(DECIMALE)}"></div><div><label>${esc(T("Poids (g)"))}</label><input id="mg" inputmode="decimal" placeholder="250"></div></div>
 <div class="rang"><div><label>${esc(T("Date"))}</label><input id="md" type="date" value="${aujourdhui()}" max="${aujourdhui()}"></div><div><label>${esc(T("Remarque"))}</label><input id="mn"></div></div><button class="b" id="mesurer">${esc(T("Enregistrer la mesure"))}</button>
 ${t.mesures.slice(0,4).map(m=>`<div class="ligne"><span>${esc(m.date)}</span><span>${esc([m.taille,m.poids].filter(x=>x).join(" · "))}</span></div>`).join("")}</section>
 <section><h2>📒 ${esc(T("Carnet"))}</h2><div class="rang"><div><label>${esc(T("Type"))}</label><select id="nt">${t.types.map(x=>`<option value="${esc(x)}">${esc(noms[x]||x)}</option>`).join("")}</select></div><div><label>${esc(T("Date"))}</label><input id="nd" type="date" value="${aujourdhui()}" max="${aujourdhui()}"></div></div>
 <label>${esc(T("Ce qui s'est passé"))}</label><textarea id="nx" rows="2"></textarea><button class="b" id="noter">${esc(T("Noter"))}</button>
 ${t.carnet.slice(0,5).map(n=>`<div class="ligne"><span>${esc(n.date)} · ${esc(n.type)}</span><span class="discret" style="text-align:right;max-width:60%">${esc(n.texte)}</span></div>`).join("")}</section>
 <section><h2>🖼 ${esc(T("Album"))}</h2><label class="b">📷 ${esc(T("Ajouter des photos"))}<input id="photos" type="file" accept="image/*" multiple hidden></label>
 <div class="album">${t.album.map(n=>`<img loading="lazy" src="/img/album/${enc(cle)}/${enc(n)}" alt="">`).join("")}</div></section>`;
 $("#fermer").addEventListener("click",()=>f.close());
 $("#mesurer").addEventListener("click",()=>mesurer(cle));
 $("#noter").addEventListener("click",()=>noter(cle));
 $("#photos").addEventListener("change",ev=>envoyer(cle,ev.target.files));
 cacherSiErreur(f);
 if(!f.open)f.showModal()}
async function poster(u,corps){return api(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(corps)})}
async function mesurer(cle){try{await poster("/api/mesure",{cle,cm:$("#mc").value,g:$("#mg").value,date:$("#md").value,note:$("#mn").value});message(T("Mesure enregistrée"));signatures.poissons="";fiche(cle);etat()}catch(e){message(e.message||T("Erreur"),1)}}
async function noter(cle){try{await poster("/api/note",{cle,type:$("#nt").value,date:$("#nd").value,texte:$("#nx").value});message(T("Noté"));fiche(cle)}catch(e){message(e.message||T("Erreur"),1)}}
async function envoyer(cle,fichiers){let n=0;for(const f of fichiers){try{await api(`/api/photo?cle=${enc(cle)}&nom=${enc(f.name)}`,{method:"POST",body:f});n++}catch(e){message(e.message||T("Erreur"),1)}}
 if(n){message(PL(n)?T("{n} photo ajoutée",{n}):T("{n} photos ajoutées",{n}));signatures.poissons="";fiche(cle);etat()}}
$("#puces").addEventListener("click",ev=>{const b=ev.target.closest(".puce");if(b)choisir(b.dataset.bac)});
$("#poissons").addEventListener("click",ev=>{const b=ev.target.closest(".t");if(b)fiche(b.dataset.cle).catch(e=>{if(e)message(e.message||T("Erreur"),1)})});
$("#lots").addEventListener("click",async ev=>{const b=ev.target.closest("button[data-action=lot]");if(!b)return;const l=b.closest(".lot");
 try{await poster("/api/lot",{id:l.dataset.lot,genre:l.querySelector(".lg").value,nombre:l.querySelector(".ln").value,date:aujourdhui()});message(T("Noté"));signatures.lots="";etat()}catch(e){message(e.message||T("Erreur"),1)}});
$("#video").addEventListener("error",()=>setTimeout(()=>{if(bacChoisi)choisir(bacChoisi)},3000));
document.addEventListener("visibilitychange",()=>{if(!document.hidden){if(bacChoisi)choisir(bacChoisi);etat()}});
etat();setInterval(etat,10000);
</script></body></html>"""

# textes de la page : tous les T("...") de PAGE, relevés ici pour ne jamais diverger de la page
CLES_PAGE = list(dict.fromkeys(re.findall(r'\bT\("([^"\\]*)"', PAGE)))


def page():
    """Page du téléphone dans la langue choisie : traductions posées en JSON sûr dans la balise <script>."""
    entete = (f"const TR = {_json_script(i18n.catalogue_web(CLES_PAGE))};\n"
              f"const LANGUE = {_json_script(i18n.langue)};\n"
              f"const DECIMALE = {_json_script(i18n.nombre(12.5))};")
    langue = i18n.langue if i18n.langue in i18n.LANGUES else "fr"
    # la langue d'abord, les traductions en dernier : rien n'est remplacé à l'intérieur du JSON posé
    return PAGE.replace("__LANGUE__", langue, 1).replace("/*TRADUCTIONS*/", entete, 1)
