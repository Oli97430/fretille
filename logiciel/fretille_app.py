"""
Frétille — l'aquarium qui veille sur ses poissons (application de bureau).

Plusieurs bacs (aquariums, bassins, bacs d'élevage), chacun avec sa caméra : nombre de poissons,
activité, poissons en surface ou immobiles, prédateurs au bassin, poissons reconnus un par un et
leur croissance, carnet de l'eau, lots d'alevins. Alertes sur le téléphone, page web sur le Wi-Fi,
plein écran pour diffuser en direct.
"""

import io
import json
import os
import queue
import secrets
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageDraw, ImageOps, ImageTk

import alertes
import demarrage
import eau
import elevage
import fiches
import graphiques
import i18n
import sante
import sauvegarde
import sources
import vision
from fretille_cam import Surveillance, etalonner, lire_evenements, lire_mesures
from i18n import nombre, pourcent, t, tn
from outils import FichierOccupe, lire_image
from registre import ALBUM, Registre, RegistreIllisible

try:
    import web
except ImportError:                     # page du téléphone pas encore installée
    web = None

ICI = Path(__file__).resolve().parent
if (ICI / "hf_cache").is_dir():
    os.environ.setdefault("HF_HOME", str(ICI / "hf_cache"))
CONFIG = Path.home() / ".fretille.json"
DEFAUTS = {
    "dossier": str(Path.home() / "Videos" / "Fretille"),
    "bacs": [],
    "analyse_toutes": 0,        # secondes entre deux analyses ; 0 = selon la machine (3 s carte graphique, 6 s sinon)
    "reconnaissance": True,
    "alerte_intrus": True,
    "demarrage_auto": False,
    "sauvegarde_dossier": "",
    "web_actif": False, "web_code": "", "web_port": 0, "web_secret": "",
    "ntfy_actif": False, "ntfy_sujet": "", "ntfy_serveur": alertes.SERVEUR,
    "langue": "",               # "" = langue de Windows
}


def T(texte):
    """Marque un texte à traduire sans le traduire (constantes définies à l'import) : t(...) à l'affichage."""
    return texte


TYPES_BAC = {"aquarium": T("Aquarium"), "bassin": T("Bassin"), "elevage": T("Bac d'élevage")}     # traduits à l'affichage

# Thème : eau profonde
FOND, CARTE, BORD = "#0b1418", "#13222a", "#1f3540"
TEXTE, DISCRET = "#e6f1f3", "#8aa5ad"
ACCENT, VERT, ROUGE, AMBRE = "#3cc6c0", "#6fd08c", "#f85149", "#f0b44c"
COULEUR_ETAT = {"ok": VERT, "surveiller": AMBRE, "alerte": ROUGE, "inconnu": DISCRET, "arrete": "#4a5f66"}
LIBELLE_ETAT = {"ok": T("Tout va bien"), "surveiller": T("À surveiller"), "alerte": T("Alerte"),
                "inconnu": T("En observation"), "arrete": T("Arrêté")}


def police(taille, gras=False):
    """Police PIL de la langue (Segoe UI ; CJK, thaï… selon la langue)."""
    return i18n.police_pil(taille, gras)


def PT(taille, gras=False):
    """Police Tk de la langue : Segoe UI (Semibold si gras) ou la famille qui a les caractères."""
    famille = i18n.famille_tk()
    if famille == "Segoe UI":
        return ("Segoe UI Semibold", taille) if gras else ("Segoe UI", taille)
    return (famille, taille, "bold") if gras else (famille, taille)


def libelle_source(source):
    source = str(source)
    if source.isdigit():
        return t("Webcam {n}", n=source)
    return t("Fenêtre : {titre}", titre=source[len(sources.PREFIXE_FENETRE):]) if sources.est_fenetre(source) else source


def _extraire(saisie, modele):
    """Partie variable de saisie selon un modèle « avant\\0après » ; None si saisie ne suit pas le modèle."""
    avant, _sep, apres = modele.partition("\0")
    if (avant or apres) and len(saisie) >= len(avant) + len(apres) and saisie.startswith(avant) and saisie.endswith(apres):
        return saisie[len(avant):len(saisie) - len(apres)]
    return None


def source_depuis_saisie(saisie):
    saisie = saisie.strip()
    for modele in (t("Webcam {n}", n="\0"), "Webcam \0"):
        reste = _extraire(saisie, modele)
        if reste is not None:
            return reste
    for modele in (t("Fenêtre : {titre}", titre="\0"), "Fenêtre : \0"):
        reste = _extraire(saisie, modele)
        if reste is not None:
            return sources.PREFIXE_FENETRE + reste
    return saisie


def derniere_mesure(mesures):
    """(« 12,5 cm », « 85 g ») d'après les dernières mesures (la main prime sur la vidéo le même jour)."""
    taille = poids = ""
    for m in sorted(mesures, key=lambda m: (m["date"], m.get("source") == "main")):
        if m.get("cm"):
            taille = t("{v} cm", v=nombre(m["cm"]))
        if m.get("g"):
            poids = t("{v} g", v=nombre(m["g"]))
    return taille, poids


def especes_affichees():
    """(libellés traduits des espèces connues, {libellé traduit: nom français stocké})."""
    noms = [x[0] for x in vision.ESPECES_DEFAUT]
    return [t(n) for n in noms], {t(n): n for n in noms}


def journaliser_erreur(e):
    try:
        with open(Path.home() / ".fretille_erreurs.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{''.join(traceback.format_exception(e))}\n")
    except OSError:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = {**DEFAUTS, **self._lire_config()}
        i18n.choisir(self.cfg.get("langue") or i18n.langue_systeme())
        self.messages = queue.Queue()
        self.verrou = threading.Lock()
        self.surveillances = {}         # bac -> Surveillance en cours
        self.images = {}                # bac -> (image, etat) la plus récente
        self.nouvelle_image = set()
        self.bilans = {}                # bac -> bilan de santé (sante.bilan)
        self.bac_courant = self.cfg["bacs"][0]["id"] if self.cfg["bacs"] else None
        self.image_affichee = None
        self.cache_vignettes = {}
        self.fenetres_fiche = {}
        self.zones_video = []
        self.modeles_prets = False
        self.fermeture = False
        self.diffusion = None
        self.serveur_web = None
        self.alertes = None
        self.detecteur = vision.Detecteur()
        self.classeur = vision.Classeur(vision.ESPECES_DEFAUT)
        try:
            self.registre = Registre(self.dossier)
        except RegistreIllisible as e:
            messagebox.showerror("Frétille", str(e))
            raise SystemExit(1)
        self.elevage = elevage.Elevage(self.dossier)
        self.f_tag, self.f_petit, self.f_vide, self.f_titre = police(18, True), police(14), police(20), police(22, True)
        self._style()
        self._construire()
        self._maj_barre_bacs()
        self._maj_evenements()
        self._maj_poissons()
        self._maj_lots()
        threading.Thread(target=self._precharger, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.quitter)
        self.root.after(30, self._boucle)
        self.root.after(1500, self._sante_periodique)
        self.root.after(15000, self._sauvegarde_auto)
        self._maj_alertes()
        if self.cfg["web_actif"]:
            self.root.after(800, lambda: self._web(True, silencieux=True))
        if self.cfg["demarrage_auto"] or "--demarrage" in sys.argv:
            self.root.after(800, self.tout_demarrer)

    # -------------------------------------------------------------- configuration
    def _lire_config(self):
        for chemin in (CONFIG, CONFIG.with_suffix(".bak")):
            try:
                cfg = json.loads(chemin.read_text(encoding="utf-8"))
                if isinstance(cfg, dict):
                    return cfg
            except (OSError, ValueError):
                pass
        return {}

    def _ecrire_config(self):
        try:
            tmp = CONFIG.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            if CONFIG.exists():
                CONFIG.replace(CONFIG.with_suffix(".bak"))
            tmp.replace(CONFIG)
        except OSError:
            pass

    @property
    def dossier(self):
        return Path(self.cfg["dossier"])

    def _bac(self, ident):
        return next((b for b in self.cfg["bacs"] if b["id"] == ident), None)

    def _analyse_toutes(self):
        if self.cfg["analyse_toutes"]:
            return float(self.cfg["analyse_toutes"])
        try:
            return 3.0 if vision.appareil() == "cuda" else 6.0
        except ImportError:
            return 6.0

    def _precharger(self):
        try:
            self.messages.put(("message", t("Chargement des modèles de vision…")))
            self.detecteur.charger()
            self.classeur.charger()
            self.registre.descripteur.charger()
            self.registre.preparer()
            self.modeles_prets = True
            self.messages.put(("modele", t("carte graphique") if vision.appareil() == "cuda" else t("processeur")))
        except Exception as e:
            self.messages.put(("erreur_modele", f"{type(e).__name__} : {e}"))

    # -------------------------------------------------------------- style
    def _style(self):
        r = self.root
        r.title("Frétille")
        r.configure(bg=FOND)
        r.geometry("1460x900")
        r.minsize(1150, 720)
        try:
            r.state("zoomed")
        except tk.TclError:
            pass
        if (ICI / "fretille.ico").exists():
            try:
                r.iconbitmap(str(ICI / "fretille.ico"))
            except tk.TclError:
                pass
        s = ttk.Style(r)
        s.theme_use("clam")
        s.configure(".", background=FOND, foreground=TEXTE, font=PT(10), bordercolor=BORD,
                    lightcolor=CARTE, darkcolor=CARTE, troughcolor=BORD)
        s.configure("Carte.TFrame", background=CARTE)
        s.configure("TLabel", background=FOND, foreground=TEXTE)
        s.configure("Carte.TLabel", background=CARTE)
        s.configure("Discret.TLabel", background=CARTE, foreground=DISCRET)
        s.configure("Titre.TLabel", background=CARTE, foreground=DISCRET, font=PT(10, True))
        s.configure("TButton", background=CARTE, foreground=TEXTE, borderwidth=1, focusthickness=0, padding=(12, 7),
                    font=PT(10, True))
        s.map("TButton", background=[("active", BORD), ("disabled", CARTE)], foreground=[("disabled", DISCRET)])
        s.configure("Vert.TButton", background=ACCENT, foreground="#06221f", bordercolor=ACCENT)
        s.map("Vert.TButton", background=[("active", "#62dbd5")])
        s.configure("Rouge.TButton", background=ROUGE, foreground="white", bordercolor=ROUGE)
        s.map("Rouge.TButton", background=[("active", "#ff7b72")])
        s.configure("TCombobox", fieldbackground=CARTE, background=CARTE, foreground=TEXTE, arrowcolor=TEXTE, padding=6)
        s.map("TCombobox", fieldbackground=[("readonly", CARTE)], foreground=[("readonly", TEXTE)])
        r.option_add("*TCombobox*Listbox.background", CARTE)
        r.option_add("*TCombobox*Listbox.foreground", TEXTE)
        r.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        s.configure("Treeview", background=CARTE, fieldbackground=CARTE, foreground=TEXTE, rowheight=50, borderwidth=0,
                    font=PT(10))
        s.configure("Treeview.Heading", background=CARTE, foreground=DISCRET, borderwidth=0, font=PT(9, True))
        s.map("Treeview", background=[("selected", "#1d4a4c")])
        s.configure("Liste.Treeview", rowheight=28)
        s.configure("TCheckbutton", background=CARTE, foreground=TEXTE)
        s.map("TCheckbutton", background=[("active", CARTE)])
        s.configure("Vertical.TScrollbar", background=CARTE, troughcolor=FOND, arrowcolor=DISCRET)
        s.configure("TNotebook", background=CARTE, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=CARTE, foreground=DISCRET, padding=(14, 6),
                    font=PT(10, True), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", BORD)], foreground=[("selected", TEXTE)])
        s.configure("TEntry", fieldbackground=CARTE, foreground=TEXTE, insertcolor=TEXTE)
        s.configure("TSpinbox", fieldbackground=CARTE, foreground=TEXTE, arrowcolor=TEXTE)
        r.option_add("*Menu.background", CARTE)
        r.option_add("*Menu.foreground", TEXTE)
        r.option_add("*Menu.activeBackground", ACCENT)

    def _carte(self, parent, titre=None, **grid):
        cadre = ttk.Frame(parent, style="Carte.TFrame", padding=14)
        cadre.grid(**grid)
        if titre:
            ttk.Label(cadre, text=titre.upper(), style="Titre.TLabel").pack(anchor="w", pady=(0, 8))
        return cadre

    # -------------------------------------------------------------- interface
    def _construire(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(2, weight=1)

        tete = tk.Frame(r, bg=FOND)
        tete.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 4))
        try:
            self.logo = ImageTk.PhotoImage(Image.open(ICI / "fretille.png").resize((48, 48), Image.LANCZOS))
            tk.Label(tete, image=self.logo, bg=FOND).pack(side="left", padx=(0, 12))
        except OSError:
            pass
        titres = tk.Frame(tete, bg=FOND)
        titres.pack(side="left")
        tk.Label(titres, text="Frétille", bg=FOND, fg=TEXTE, font=PT(20, True)).pack(anchor="w")
        tk.Label(titres, text=t("l'aquarium qui veille sur ses poissons"), bg=FOND, fg=DISCRET, font=PT(10)).pack(anchor="w")
        outils_ = tk.Frame(tete, bg=FOND)
        outils_.pack(side="left", padx=(28, 0))
        for texte, commande in (("📊  " + t("Santé"), self.fenetre_sante), ("💧  " + t("Eau"), self.fenetre_eau),
                                ("🥚  " + t("Élevage"), self.fenetre_elevage), ("📺  " + t("Plein écran"), self.plein_ecran),
                                ("📱  " + t("Téléphone"), self.telephone)):
            ttk.Button(outils_, text=texte, command=commande).pack(side="left", padx=(0, 8))
        ttk.Button(tete, text="⚙  " + t("Réglages"), command=self.reglages).pack(side="right")
        self.lbl_modele = tk.Label(tete, text=t("Modèles : chargement…"), bg=FOND, fg=DISCRET, font=PT(10))
        self.lbl_modele.pack(side="right", padx=16)
        r.bind("<F11>", lambda _e: self.plein_ecran())

        # barre des bacs : une pastille par bac (couleur = santé), puis les commandes du bac affiché
        self.barre = tk.Frame(r, bg=FOND)
        self.barre.grid(row=1, column=0, sticky="ew", padx=20, pady=(6, 10))
        self.pastilles = tk.Frame(self.barre, bg=FOND)
        self.pastilles.pack(side="left")
        commandes = tk.Frame(self.barre, bg=FOND)
        commandes.pack(side="right")
        self.btn_bac = ttk.Button(commandes, text="▶  " + t("Démarrer"), style="Vert.TButton", command=self.basculer_bac)
        self.btn_bac.pack(side="left")
        ttk.Button(commandes, text="🎬  " + t("Clip 30 s"), command=self.clip_maintenant).pack(side="left", padx=(8, 0))
        ttk.Button(commandes, text="🛠  " + t("Ce bac…"), command=lambda: self.dialogue_bac(self._bac(self.bac_courant))
                   ).pack(side="left", padx=(8, 0))
        ttk.Button(commandes, text="▶▶  " + t("Tout démarrer"), command=self.tout_demarrer).pack(side="left", padx=(8, 0))

        corps = tk.Frame(r, bg=FOND)
        corps.grid(row=2, column=0, sticky="nsew", padx=20)
        corps.columnconfigure(0, weight=1)
        corps.rowconfigure(0, weight=1)
        cadre_video = tk.Frame(corps, bg="#000000", highlightthickness=1, highlightbackground=BORD)
        cadre_video.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.video = tk.Label(cadre_video, bg="#050a0c")
        self.video.pack(fill="both", expand=True)
        self.video.bind("<Configure>", lambda e: self._afficher_vide() if self.bac_courant not in self.surveillances else None)
        for bouton in ("<Button-1>", "<Button-3>"):
            self.video.bind(bouton, self._menu_video)

        droite = tk.Frame(corps, bg=FOND, width=430)
        droite.grid(row=0, column=1, sticky="ns")
        droite.grid_propagate(False)
        droite.columnconfigure(0, weight=1)
        droite.rowconfigure(1, weight=1)

        c = self._carte(droite, t("État du bac"), row=0, column=0, sticky="ew", pady=(0, 12))
        self.lbl_etat_bac = tk.Label(c, text="—", bg=CARTE, fg=DISCRET, font=PT(18, True), anchor="w")
        self.lbl_etat_bac.pack(fill="x")
        self.lbl_compte = tk.Label(c, text="", bg=CARTE, fg=TEXTE, font=PT(11), anchor="w")
        self.lbl_compte.pack(fill="x", pady=(2, 6))
        self.lbl_raisons = tk.Label(c, text="", bg=CARTE, fg=DISCRET, font=PT(10), justify="left",
                                    wraplength=390, anchor="w")
        self.lbl_raisons.pack(fill="x")
        tk.Label(c, text=t("ACTIVITÉ DES DEUX DERNIÈRES HEURES"), bg=CARTE, fg=DISCRET,
                 font=PT(8, True)).pack(anchor="w", pady=(10, 2))
        self.ligne_activite = tk.Label(c, bg=CARTE)
        self.ligne_activite.pack(fill="x")

        c = self._carte(droite, None, row=1, column=0, sticky="nsew")
        onglets = ttk.Notebook(c)
        onglets.pack(fill="both", expand=True)
        cadre = ttk.Frame(onglets, style="Carte.TFrame")
        onglets.add(cadre, text=t("Événements"))
        self.arbre_ev = self._arbre(cadre, (("quand", t("QUAND"), 86), ("quoi", t("ÉVÉNEMENT"), 200)))
        self.arbre_ev.tag_configure("predateur", foreground=ROUGE)
        self.arbre_ev.tag_configure("sante", foreground=AMBRE)
        self.arbre_ev.bind("<Double-1>", self._ouvrir_evenement)
        self.lignes_ev = {}
        cadre = ttk.Frame(onglets, style="Carte.TFrame")
        onglets.add(cadre, text=t("Mes poissons"))
        bas = tk.Frame(cadre, bg=CARTE)
        bas.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(bas, text="➕  " + t("Nouveau poisson"), command=self.ajouter_poisson).pack(side="left")
        self.arbre_p = self._arbre(cadre, (("nom", t("NOM"), 120), ("bac", t("BAC"), 90), ("taille", t("TAILLE"), 70)))
        self.arbre_p.bind("<Double-1>", lambda e: self.fiche_poisson(self.arbre_p.identify_row(e.y))
                          if self.arbre_p.identify_row(e.y) else None)
        self.arbre_p.bind("<Button-3>", self._menu_poisson)
        cadre = ttk.Frame(onglets, style="Carte.TFrame")
        onglets.add(cadre, text=t("Élevage"))
        self.arbre_l = self._arbre(cadre, (("nom", t("LOT"), 120), ("age", t("ÂGE"), 60), ("effectif", t("ALEVINS"), 80)),
                                   vignettes=False)
        self.arbre_l.bind("<Double-1>", lambda _e: self.fenetre_elevage())
        self.vignettes = {}

        pied = ttk.Frame(r, style="Carte.TFrame", padding=(20, 8))
        pied.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(pied, text=t("Clic sur un poisson de la vidéo : le nommer ou ouvrir sa fiche   ·   double-clic sur un "
                               "événement : sa photo ou son clip"), style="Discret.TLabel").pack(side="left")
        self.lbl_etat = ttk.Label(pied, text=t("Prêt"), style="Discret.TLabel", width=60, anchor="e")
        self.lbl_etat.pack(side="right")

    def _arbre(self, parent, colonnes, vignettes=True):
        arbre = ttk.Treeview(parent, columns=[c[0] for c in colonnes], show="tree headings" if vignettes else "headings",
                             selectmode="browse", style="Treeview" if vignettes else "Liste.Treeview")
        if vignettes:
            arbre.heading("#0", text="")
            arbre.column("#0", width=86, stretch=False)
        for col, titre, larg in colonnes:
            arbre.heading(col, text=titre, anchor="w")
            arbre.column(col, width=larg, anchor="w")
        defil = ttk.Scrollbar(parent, orient="vertical", command=arbre.yview)
        arbre.configure(yscrollcommand=defil.set)
        arbre.pack(side="left", fill="both", expand=True)
        defil.pack(side="right", fill="y")
        return arbre

    def _vignette(self, chemin, taille):
        try:
            cle = (str(chemin), taille, os.stat(chemin).st_mtime_ns)
            if cle in self.cache_vignettes:
                return self.cache_vignettes[cle]
            with Image.open(chemin) as img:
                img.draft("RGB", (taille[0] * 2, taille[1] * 2))
                img = ImageOps.exif_transpose(img)
                img.thumbnail(taille)
                vignette = ImageTk.PhotoImage(img)
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        if len(self.cache_vignettes) > 1500:
            self.cache_vignettes.clear()
        self.cache_vignettes[cle] = vignette
        return vignette

    # -------------------------------------------------------------- bacs
    def _maj_barre_bacs(self):
        for w in self.pastilles.winfo_children():
            w.destroy()
        if not self.cfg["bacs"]:
            tk.Label(self.pastilles, text=t("Aucun bac : ajoutez votre aquarium ou votre bassin →"), bg=FOND, fg=DISCRET,
                     font=PT(11)).pack(side="left", padx=(0, 12))
        for bac in self.cfg["bacs"]:
            etat = self.bilans.get(bac["id"], {}).get("etat", "inconnu") if bac["id"] in self.surveillances else "arrete"
            courant = bac["id"] == self.bac_courant
            nb = self.images.get(bac["id"], (None, {}))[1].get("nb") if bac["id"] in self.surveillances else None
            texte = f"●  {bac['nom']}" + (f"   {nb} 🐟" if nb is not None else "")
            b = tk.Label(self.pastilles, text=texte, bg=BORD if courant else CARTE, fg=COULEUR_ETAT[etat],
                         font=PT(11, True), padx=14, pady=7, cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", lambda _e, i=bac["id"]: self.choisir_bac(i))
        plus = tk.Label(self.pastilles, text="＋  " + t("Bac"), bg=FOND, fg=ACCENT, font=PT(11, True), padx=10,
                        pady=7, cursor="hand2")
        plus.pack(side="left")
        plus.bind("<Button-1>", lambda _e: self.dialogue_bac(None))
        en_cours = self.bac_courant in self.surveillances
        self.btn_bac.configure(text="■  " + t("Arrêter") if en_cours else "▶  " + t("Démarrer"), style="Rouge.TButton" if en_cours else "Vert.TButton",
                               state="normal" if self.bac_courant else "disabled")

    def choisir_bac(self, ident):
        self.bac_courant = ident
        self.image_affichee = None
        self._maj_barre_bacs()
        self._maj_etat_bac()
        if ident not in self.surveillances:
            self._afficher_vide()

    def basculer_bac(self):
        if self.bac_courant in self.surveillances:
            self.arreter(self.bac_courant)
        elif self.bac_courant:
            self.demarrer(self.bac_courant)

    def tout_demarrer(self):
        for bac in self.cfg["bacs"]:
            if bac.get("actif", True) and bac["id"] not in self.surveillances:
                self.demarrer(bac["id"])

    def demarrer(self, ident):
        bac = self._bac(ident)
        if bac is None or ident in self.surveillances:
            return
        if not str(bac.get("source", "")).strip():
            messagebox.showinfo("Frétille", t("Choisissez d'abord la caméra de « {bac} » (bouton « Ce bac… »).", bac=bac["nom"]))
            return
        s = Surveillance({**bac, "reconnaissance": self.cfg["reconnaissance"] and bac.get("reconnaissance", True)},
                         self.dossier, self.detecteur, self.classeur, self.registre, self._analyse_toutes(),
                         sur_image=lambda image, etat, i=ident: self._sur_image(i, image, etat),
                         sur_evenement=lambda ligne: self.messages.put(("evenement", ligne)),
                         sur_message=lambda texte: self.messages.put(("message", texte)),
                         sur_figee=lambda figee, i=ident: self.messages.put(("figee", (i, figee))),
                         sur_fin=lambda ok_, i=ident: self.messages.put(("fin", (i, ok_))))
        if bac.get("type") == "bassin" and not self.cfg["alerte_intrus"]:
            s.classeur = None               # prédateurs non surveillés
        self.bilans.pop(ident, None)
        self.surveillances[ident] = s
        s.demarrer()
        self.lbl_etat.configure(text=t("Surveillance de « {bac} »…", bac=bac["nom"]))
        self._maj_barre_bacs()

    def arreter(self, ident):
        s = self.surveillances.get(ident)
        if s:
            s.arreter()
            self.lbl_etat.configure(text=t("Arrêt en cours…"))

    def _sur_image(self, ident, image, etat):
        with self.verrou:
            self.images[ident] = (image, etat)
            self.nouvelle_image.add(ident)

    def clip_maintenant(self):
        s = self.surveillances.get(self.bac_courant)
        if s:
            s.enregistrer(30)
            self.lbl_etat.configure(text="🎬 " + t("Clip de 30 s en cours (5 s d'avant compris)…"))
        else:
            self.lbl_etat.configure(text=t("Démarrez d'abord la surveillance de ce bac"))

    # -------------------------------------------------------------- boucle d'affichage
    def _tk_vivant(self):
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def _boucle(self):
        if self.fermeture and not self._tk_vivant():
            return
        if self.diffusion is not None and not self.diffusion.winfo_exists():
            self._quitter_diffusion()
        try:
            self._traiter_messages()
            with self.verrou:
                frais = self.bac_courant in self.nouvelle_image
                self.nouvelle_image.discard(self.bac_courant)
                donnees = self.images.get(self.bac_courant)
            if frais and donnees and self.bac_courant in self.surveillances:
                image, etat = donnees
                if self.diffusion is not None:
                    self._afficher_diffusion(image, etat)
                else:
                    self._afficher_image(image, etat)
        except Exception as e:
            journaliser_erreur(e)
            self.lbl_etat.configure(text=t("Erreur d'affichage : {nom}", nom=type(e).__name__))
        finally:
            if self._tk_vivant():
                self.root.after(40, self._boucle)

    def _traiter_messages(self):
        while not self.messages.empty():
            genre, contenu = self.messages.get()
            if genre == "message":
                self.lbl_etat.configure(text=contenu)
            elif genre == "modele":
                self.lbl_modele.configure(text=t("Modèles prêts ({appareil})", appareil=contenu))
                self.lbl_etat.configure(text=t("Prêt"))
            elif genre == "erreur_modele":
                self.lbl_modele.configure(text=t("Modèles indisponibles"), fg=ROUGE)
                self.lbl_etat.configure(text=contenu)
            elif genre == "evenement":
                self._sur_evenement(contenu)
            elif genre == "poissons":
                self._maj_poissons()
                self._maj_lots()
            elif genre == "outil":
                bac, image, outil, apres, parent = contenu
                if parent.winfo_exists():
                    if image is None:
                        messagebox.showerror(t("Caméra"), t("Aucune image de cette caméra."), parent=parent)
                    else:
                        self._fenetre_outil(bac, image, outil, apres, parent)
                self.lbl_etat.configure(text=t("Prêt"))
            elif genre == "webcams":
                self._montrer_webcams(*contenu)
            elif genre == "bilans":
                self.bilans.update(contenu)
                self._maj_barre_bacs()
                self._maj_etat_bac()
            elif genre == "figee":
                ident, figee = contenu
                bac = self._bac(ident)
                if figee and bac:
                    self.root.bell()
                    self.lbl_etat.configure(text=t("Image figée : « {bac} ». Relancez le direct dans le lecteur.",
                                                   bac=bac["nom"]))
                    if self.alertes:
                        self.alertes.en_fond(self.alertes.signaler, f"figee:{ident}:{int(time.time() // 1800)}",
                                             t("{bac} : image figée", bac=bac["nom"]),
                                             t("La caméra ne montre plus d'image vivante depuis une minute."), 4,
                                             ("warning", "video_camera"))
            elif genre == "fin":
                ident, ok_ = contenu
                self.surveillances.pop(ident, None)
                self.bilans.pop(ident, None)
                with self.verrou:
                    self.images.pop(ident, None)
                if self.fermeture and not self.surveillances:
                    self.root.destroy()
                    return
                if not ok_ and self.diffusion is None:
                    messagebox.showerror(t("Source vidéo"), self.lbl_etat.cget("text"))
                if ident == self.bac_courant:
                    self._afficher_vide()
                self._maj_barre_bacs()

    def _sur_evenement(self, ligne):
        self._maj_evenements()
        if ligne.get("genre") == "predateur":
            self.root.bell()
        if ligne.get("alerte") and self.alertes:
            self.alertes.en_fond(self.alertes.signaler, f"evenement:{ligne['id']}",
                                 t("{bac} : {titre}", bac=ligne["nom_bac"], titre=ligne["titre"]),
                                 ligne["detail"], 5 if ligne["genre"] == "predateur" else 4,
                                 ("rotating_light",) if ligne["genre"] == "predateur" else ("warning", "fish"))
        self.lbl_etat.configure(text=t("{bac} : {titre}", bac=ligne["nom_bac"], titre=ligne["titre"]))

    # -------------------------------------------------------------- vidéo
    def _afficher_image(self, image, etat):
        lw, lh = max(self.video.winfo_width(), 320), max(self.video.winfo_height(), 240)
        h, w = image.shape[:2]
        e = min(lw / w, lh / h)
        nw, nh = int(w * e), int(h * e)
        img = Image.fromarray(cv2.cvtColor(cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(img, "RGBA")
        ox, oy = (self.video.winfo_width() - nw) // 2, (self.video.winfo_height() - nh) // 2
        bac = self._bac(etat.get("bac")) or {}
        if bac.get("surface") is not None and bac.get("type") != "bassin":
            y = int(bac["surface"] * nh)
            for x in range(0, nw, 24):
                d.line([(x, y), (x + 12, y)], fill=(60, 198, 192, 140), width=2)
        self.zones_video = []
        for numero, boite, texte, sure, cle in etat.get("pistes", []):
            x, y, bw, bh = (int(v * e) for v in boite)
            nomme = bool(texte) and not texte.endswith("?")
            if nomme:
                d.rounded_rectangle([x, y, x + bw, y + bh], radius=8, outline=(240, 180, 76, 255), width=3)
            else:
                d.rounded_rectangle([x, y, x + bw, y + bh], radius=8, outline=(60, 198, 192, 170 if sure else 90), width=2)
            if texte:
                tw = d.textlength(texte, font=self.f_tag)
                yt = max(0, y - 32)
                d.rounded_rectangle([x, yt, x + tw + 18, yt + 28], radius=8, fill=(11, 20, 24, 215))
                d.text((x + 9, yt + 3), texte, font=self.f_tag, fill=(230, 241, 243, 255 if nomme else 170))
            self.zones_video.append((ox + x, oy + y - 32, ox + x + max(bw, 60), oy + y + bh, numero, texte, cle))
        for boite, etiquette, danger in etat.get("intrus", []):
            x, y, bw, bh = (int(v * e) for v in boite)
            couleur = (248, 81, 73, 255) if danger else (138, 165, 173, 200)
            d.rounded_rectangle([x, y, x + bw, y + bh], radius=8, outline=couleur, width=4)
            tw = d.textlength(etiquette, font=self.f_tag)
            d.rounded_rectangle([x, max(0, y - 32), x + tw + 18, max(0, y - 32) + 28], radius=8, fill=couleur)
            d.text((x + 9, max(0, y - 32) + 3), etiquette, font=self.f_tag, fill="white")
        entete = f"{bac.get('nom', '')}   " + tn("{n} poisson", "{n} poissons", etat.get("nb", 0))
        d.rounded_rectangle([14, 14, 30 + d.textlength(entete, font=self.f_petit), 46], radius=14, fill=(11, 20, 24, 200))
        d.text((22, 20), entete, font=self.f_petit, fill=(230, 241, 243))
        if etat.get("enregistrement"):
            d.rounded_rectangle([nw - 150, 14, nw - 70, 46], radius=14, fill=(248, 81, 73, 230))
            d.text((nw - 138, 20), "● " + t("REC"), font=self.f_petit, fill="white")
        d.text((nw - 60, 20), t("{v} i/s", v=f"{etat.get('fps', 0):.0f}"), font=self.f_petit, fill=(200, 215, 220, 200))
        if etat.get("figee_s"):
            texte = t("IMAGE FIGÉE depuis {n} min — relancez le direct dans le lecteur", n=int(etat["figee_s"] // 60))
            tw = d.textlength(texte, font=self.f_petit)
            d.rounded_rectangle([(nw - tw) / 2 - 18, nh - 62, (nw + tw) / 2 + 18, nh - 18], radius=14, fill=(248, 81, 73, 235))
            d.text(((nw - tw) / 2, nh - 54), texte, font=self.f_petit, fill="white")
        if isinstance(self.image_affichee, ImageTk.PhotoImage) and self.image_affichee.width() == nw \
                and self.image_affichee.height() == nh and self.video.cget("image") == str(self.image_affichee):
            self.image_affichee.paste(img)
        else:
            self.image_affichee = ImageTk.PhotoImage(img)
            self.video.configure(image=self.image_affichee)

    def _afficher_vide(self):
        lw, lh = max(self.video.winfo_width(), 320), max(self.video.winfo_height(), 240)
        img = Image.new("RGB", (lw, lh), (5, 10, 12))
        d = ImageDraw.Draw(img)
        try:
            logo = Image.open(ICI / "fretille.png").convert("RGBA").resize((128, 128), Image.LANCZOS)
            img.paste(logo, ((lw - 128) // 2, lh // 2 - 150), logo)
        except OSError:
            pass
        bac = self._bac(self.bac_courant)
        texte = (t("« {bac} » : cliquez sur « Démarrer » pour le surveiller", bac=bac["nom"]) if bac else
                 t("Ajoutez votre premier bac avec « ＋ Bac »"))
        d.text(((lw - d.textlength(texte, font=self.f_vide)) / 2, lh / 2 + 10), texte, font=self.f_vide, fill=(138, 165, 173))
        self.image_affichee = ImageTk.PhotoImage(img)
        self.video.configure(image=self.image_affichee)

    def _menu_video(self, ev):
        """Clic sur un poisson : poisson reconnu → sa fiche ; sinon menu pour le nommer."""
        s = self.surveillances.get(self.bac_courant)
        if not s:
            return
        zone = next((z for z in reversed(self.zones_video) if z[0] <= ev.x <= z[2] and z[1] <= ev.y <= z[3]), None)
        if zone is None:
            return
        numero, texte, cle = zone[4], zone[5], zone[6]
        if cle in self.registre.fiches and ev.num == 1 and texte and not texte.endswith("?"):
            self.fiche_poisson(cle)
            return
        menu = tk.Menu(self.root, tearoff=0)
        if cle in self.registre.fiches:
            menu.add_command(label="🐟  " + t("Fiche de {nom}…", nom=self.registre.fiches[cle]["nom"]),
                             command=lambda: self.fiche_poisson(cle))
            menu.add_separator()
        menu.add_command(label=t("Ce poisson est :"), state="disabled")
        bac = self._bac(self.bac_courant)
        poissons = sorted(self.registre.copie_fiches().items(),
                          key=lambda x: (x[1].get("bac") != (bac or {}).get("id"), x[1]["nom"]))
        for autre, fiche in poissons[:40]:
            menu.add_command(label=fiche["nom"], command=lambda c=autre: self._nommer(numero, c))
        menu.add_separator()
        menu.add_command(label=t("Nouveau poisson…"), command=lambda: self._nommer_nouveau(numero))
        menu.tk_popup(ev.x_root, ev.y_root)

    def _nommer(self, numero, cle):
        s = self.surveillances.get(self.bac_courant)
        if s and not s.nommer_piste(numero, cle):
            self.lbl_etat.configure(text=t("Ce poisson n'est plus suivi : recliquez dessus quand il réapparaît"))

    def _nommer_nouveau(self, numero):
        resultat = self._dialogue_poisson(t("Nouveau poisson"))
        if resultat:
            cle = self.registre.creer(resultat[0], resultat[1])
            self.registre.modifier(cle, bac=self.bac_courant or "")
            self._maj_poissons()
            self._nommer(numero, cle)

    # -------------------------------------------------------------- panneau de droite
    def _maj_etat_bac(self):
        ident = self.bac_courant
        bac = self._bac(ident)
        if not bac:
            self.lbl_etat_bac.configure(text="—", fg=DISCRET)
            self.lbl_compte.configure(text="")
            self.lbl_raisons.configure(text="")
            return
        en_cours = ident in self.surveillances
        b = self.bilans.get(ident) or {}
        etat = b.get("etat", "inconnu") if en_cours else "arrete"
        self.lbl_etat_bac.configure(text=t("{bac} : {etat}", bac=bac["nom"], etat=t(LIBELLE_ETAT[etat])), fg=COULEUR_ETAT[etat])
        compte = []
        if b.get("nb") is not None:
            if b.get("attendus"):
                compte.append(tn("{n} poisson vu sur {attendus} attendus", "{n} poissons vus sur {attendus} attendus",
                                 b["nb"], attendus=b["attendus"]))
            else:
                compte.append(tn("{n} poisson vu", "{n} poissons vus", b["nb"]))
        compte.append(t(TYPES_BAC[bac.get("type")]) if bac.get("type") in TYPES_BAC else t("Bac"))
        self.lbl_compte.configure(text="   ·   ".join(compte))
        raisons = list(b.get("raisons", [])) if en_cours else [t("Surveillance arrêtée")]
        tests = eau.lire(self.dossier, ident)
        raisons += [texte for n, texte in eau.avis(tests, bac) if n in ("alerte", "surveiller")]
        self.lbl_raisons.configure(text="\n".join(f"• {r}" for r in raisons[:6]))
        try:
            lignes = lire_mesures(self.dossier, ident, date.today())[-120:]
        except (OSError, FichierOccupe):
            lignes = []
        self.ligne_activite.image = ImageTk.PhotoImage(graphiques.petite_ligne([l["activite"] for l in lignes], (390, 46)))
        self.ligne_activite.configure(image=self.ligne_activite.image)

    def _sante_periodique(self):
        """Toutes les deux minutes : bilan de santé de chaque bac surveillé (dans un fil), alertes dues."""
        actifs = [self._bac(i) for i in list(self.surveillances)]
        actifs = [b for b in actifs if b]
        tous = [dict(b) for b in self.cfg["bacs"]]

        def travail():
            bilans, dues = {}, []
            aujourd_hui = date.today()
            for bac in actifs:
                try:
                    lignes = lire_mesures(self.dossier, bac["id"], aujourd_hui)
                    hist = sante.historique_jours(lambda i, j: lire_mesures(self.dossier, i, j), bac["id"], aujourd_hui)
                    bilans[bac["id"]] = b = sante.bilan(bac, lignes, hist)
                    dues += b["alertes"]
                except (OSError, FichierOccupe, ValueError) as e:
                    self.messages.put(("message", t("Bilan de santé impossible : {erreur}", erreur=e)))
            for bac in tous:
                try:
                    tests = eau.lire(self.dossier, bac["id"])
                except (OSError, ValueError):
                    continue
                for niveau, texte in eau.avis(tests, bac):
                    if niveau == "alerte":      # une alerte par test inquiétant, pas une par jour
                        dues.append((f"eau:{bac['id']}:{texte[:12]}:{tests[-1]['date']}",
                                     t("{bac} : eau à corriger", bac=bac["nom"]), texte, 4))
            self.messages.put(("bilans", bilans))
            a = self.alertes
            if a and dues:
                envoyees = a.verifier(dues)
                if envoyees:
                    self.messages.put(("message", tn("{n} alerte envoyée au téléphone", "{n} alertes envoyées au téléphone",
                                                     len(envoyees))))
        threading.Thread(target=travail, daemon=True).start()
        if self._tk_vivant():
            self.root.after(120000, self._sante_periodique)

    def _maj_evenements(self):
        a = self.arbre_ev
        a.delete(*a.get_children())
        self.lignes_ev.clear()
        for k in [k for k in self.vignettes if k.startswith("ev")]:
            del self.vignettes[k]
        try:
            lignes = lire_evenements(self.dossier)[-200:]
        except (OSError, FichierOccupe):
            lignes = []
        aujourd_hui = date.today().isoformat()
        for l in reversed(lignes):
            bac = self._bac(l.get("bac"))
            quand = l["heure"][:5] if l["date"] == aujourd_hui else f"{fiches.date_courte(l['date'])} {l['heure'][:5]}"
            vignette = self._vignette(self.dossier / l["photo"], (78, 46)) if l.get("photo") else None
            iid = a.insert("", "end", image=vignette or "", tags=(l.get("genre", ""),),
                           values=(quand, f"{l['titre']}" + (f" — {bac['nom']}" if bac and len(self.cfg['bacs']) > 1 else "")))
            if vignette:
                self.vignettes[f"ev{iid}"] = vignette
            self.lignes_ev[iid] = l

    def _ouvrir_evenement(self, ev):
        iid = self.arbre_ev.identify_row(ev.y)
        l = self.lignes_ev.get(iid)
        if not l:
            return
        for cle in ("video", "photo"):
            if l.get(cle) and (self.dossier / l[cle]).is_file():
                os.startfile(self.dossier / l[cle])
                return

    def _maj_poissons(self):
        for cle in [c for c in self.fenetres_fiche if c not in self.registre.fiches]:
            self._fermer_fiche(cle)
        a = self.arbre_p
        a.delete(*a.get_children())
        for cle, f in sorted(self.registre.copie_fiches().items(), key=lambda x: x[1]["nom"].lower()):
            p = self.registre.photo_profil(cle)
            vignette = self._vignette(p, (78, 46)) if p else None
            bac = self._bac(f.get("bac"))
            taille, _poids = derniere_mesure(f.get("mesures", []))
            a.insert("", "end", iid=cle, image=vignette or "", values=(f["nom"], bac["nom"] if bac else "—", taille or "—"))
            if vignette:
                self.vignettes[f"p{cle}"] = vignette

    def _maj_lots(self):
        a = self.arbre_l
        a.delete(*a.get_children())
        for lot in self.elevage.recharger():
            if lot.get("clos"):
                continue
            b = elevage.bilan(lot)
            a.insert("", "end", iid=lot["id"], values=(lot["nom"], t("{n} j", n=b["age_j"]), b["effectif"]))

    def _menu_poisson(self, ev):
        cle = self.arbre_p.identify_row(ev.y)
        if not cle:
            return
        self.arbre_p.selection_set(cle)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="🐟  " + t("Fiche…"), command=lambda: self.fiche_poisson(cle))
        menu.add_command(label="📏  " + t("Croissance…"), command=lambda: self.fiche_poisson(cle, 1))
        menu.add_command(label="🖼  " + t("Photos…"), command=lambda: self.fiche_poisson(cle, 2))
        menu.add_command(label="📒  " + t("Carnet…"), command=lambda: self.fiche_poisson(cle, 3))
        fusion = tk.Menu(menu, tearoff=0)
        for autre, f in sorted(self.registre.copie_fiches().items(), key=lambda x: x[1]["nom"]):
            if autre != cle:
                fusion.add_command(label=f["nom"], command=lambda a=autre: self._fusionner(cle, a))
        menu.add_cascade(label=t("C'est le même poisson que"), menu=fusion)
        menu.add_separator()
        menu.add_command(label=t("Supprimer la fiche…"), command=lambda: self._supprimer_poisson(cle))
        menu.tk_popup(ev.x_root, ev.y_root)

    def _fusionner(self, source, cible):
        fs, fc = self.registre.fiches[source], self.registre.fiches[cible]
        if messagebox.askyesno(t("Même poisson"), t("« {source} » et « {cible} » sont le même poisson ?\n"
                                                    "Tout est regroupé sous « {cible} ».", source=fs["nom"], cible=fc["nom"])):
            self.registre.fusionner(source, cible)
            self._maj_poissons()

    def _supprimer_poisson(self, cle):
        fiche = self.registre.fiches.get(cle)
        if fiche and messagebox.askyesno(t("Supprimer"), t("Supprimer la fiche, la galerie et l'album de « {nom} » ?",
                                                           nom=fiche["nom"])):
            self.registre.supprimer(cle)
            self._maj_poissons()

    def _fermer_fiche(self, cle):
        f = self.fenetres_fiche.pop(cle, None)
        if f is not None and f.winfo_exists():
            f.destroy()

    def _dialogue_poisson(self, titre, nom="", espece=None):
        f = tk.Toplevel(self.root, bg=CARTE, padx=20, pady=16)
        f.title(titre)
        f.transient(self.root)
        f.grab_set()
        resultat = []
        libelles, especes = especes_affichees()
        v_nom, v_espece = tk.StringVar(value=nom), tk.StringVar(value=t(espece or vision.ESPECES_DEFAUT[0][0]))
        ttk.Label(f, text=t("Nom"), style="Carte.TLabel").grid(row=0, column=0, sticky="w", pady=5, padx=(0, 12))
        e = ttk.Entry(f, textvariable=v_nom, width=30)
        e.grid(row=0, column=1)
        e.focus_set()
        ttk.Label(f, text=t("Espèce"), style="Carte.TLabel").grid(row=1, column=0, sticky="w", pady=5, padx=(0, 12))
        ttk.Combobox(f, textvariable=v_espece, values=libelles, width=28).grid(row=1, column=1)

        def valider(_=None):
            if v_nom.get().strip():
                saisie = v_espece.get().strip()
                resultat.append((v_nom.get().strip(), especes.get(saisie, saisie)))
                f.destroy()

        f.bind("<Return>", valider)
        ttk.Button(f, text=t("Valider"), style="Vert.TButton", command=valider).grid(row=2, column=1, sticky="e", pady=(12, 0))
        self.root.wait_window(f)
        return resultat[0] if resultat else None

    def ajouter_poisson(self):
        resultat = self._dialogue_poisson(t("Nouveau poisson"))
        if resultat:
            cle = self.registre.creer(*resultat)
            self.registre.modifier(cle, bac=self.bac_courant or "")
            self._maj_poissons()
            self.fiche_poisson(cle, 2)

    def _importer_photos(self, cle, chemins):
        """Photos apprises pour la reconnaissance : le poisson y est trouvé, isolé, ajouté à sa galerie."""
        def travail():
            n = 0
            for chemin in chemins:
                image = lire_image(chemin)
                if image is None:
                    continue
                try:
                    poissons, _i = self.detecteur.detecter(image)
                except Exception as e:
                    self.messages.put(("message", t("Photo non analysée : {erreur}", erreur=e)))
                    continue
                if poissons:
                    vue = vision.isoler(image, poissons[0][1])
                    if vue is not None and self.registre.ajouter_exemple(cle, vue, f"import_{int(time.time() * 1000)}.jpg"):
                        n += 1
            self.messages.put(("message", tn("{n} vue apprise pour {nom}", "{n} vues apprises pour {nom}", n,
                                             nom=self.registre.fiches.get(cle, {}).get("nom", ""))))
            self.messages.put(("poissons", None))
        self.lbl_etat.configure(text=t("Analyse des photos…"))
        threading.Thread(target=travail, daemon=True).start()

    def fiche_poisson(self, cle, onglet=0):
        """Tout sur un poisson, modifiable à tout moment : identité (bac, naissance, sexe…), croissance,
        album photo et vues de reconnaissance, carnet. Une seule fenêtre par poisson."""
        cle = self.registre.resoudre(cle)
        if not cle:
            return
        ouverte = self.fenetres_fiche.get(cle)
        if ouverte is not None and ouverte.winfo_exists():
            ouverte.deiconify()
            ouverte.lift()
            ouverte.onglets.select(onglet)
            return
        f = tk.Toplevel(self.root, bg=CARTE, padx=18, pady=14)
        self.fenetres_fiche[cle] = f
        f.transient(self.root)
        f.geometry("1000x780")
        f.minsize(880, 660)

        # --- bandeau : portrait, nom, identité en une ligne, chiffres clés
        tete = tk.Frame(f, bg=CARTE)
        tete.pack(fill="x", pady=(0, 12))
        cadre_portrait = tk.Frame(tete, bg=FOND, width=150, height=150, cursor="hand2")
        cadre_portrait.pack(side="left")
        cadre_portrait.pack_propagate(False)
        portrait = tk.Label(cadre_portrait, bg=FOND, fg=DISCRET, font=PT(9), cursor="hand2")
        portrait.pack(fill="both", expand=True)
        textes = tk.Frame(tete, bg=CARTE)
        textes.pack(side="left", padx=18, fill="both", expand=True)
        lbl_nom = tk.Label(textes, bg=CARTE, fg=TEXTE, font=PT(24, True))
        lbl_nom.pack(anchor="w")
        lbl_resume = tk.Label(textes, bg=CARTE, fg=AMBRE, font=PT(12, True))
        lbl_resume.pack(anchor="w")
        lbl_chiffres = tk.Label(textes, bg=CARTE, fg=DISCRET, font=PT(10), justify="left")
        lbl_chiffres.pack(anchor="w", pady=(8, 0))
        boutons = tk.Frame(tete, bg=CARTE)
        boutons.pack(side="right", anchor="n")
        ttk.Button(boutons, text="🗑  " + t("Supprimer la fiche"), command=lambda: self._supprimer_poisson(cle)).pack(fill="x")

        def bandeau():
            fi = self.registre.copie_fiches().get(cle)
            if not fi:
                return
            f.title(t("Fiche de {nom}", nom=fi["nom"]))
            lbl_nom.configure(text=fi["nom"])
            lbl_resume.configure(text=fiches.resume_identite(fi))
            chiffres = []
            bac = self._bac(fi.get("bac"))
            if bac:
                chiffres.append(t("dans « {bac} »", bac=bac["nom"]))
            taille, poids = derniere_mesure(fi.get("mesures", []))
            if taille:
                chiffres.append(f"{taille}")
            if poids:
                chiffres.append(f"{poids}")
            if fi.get("arrivee"):
                chiffres.append(t("chez vous depuis {date}", date=fiches.date_lisible(fi["arrivee"])))
            chiffres = chiffres or [t("Pas encore de mesure")]
            lbl_chiffres.configure(text="   ·   ".join(chiffres) + ("\n" + fi["signes"] if fi.get("signes") else ""))
            chemin = self.registre.photo_profil(cle)
            portrait.image = self._vignette(chemin, (150, 150)) if chemin else None
            portrait.configure(image=portrait.image or "", text="" if portrait.image else t("Pas encore\nde photo"))

        onglets = ttk.Notebook(f)
        onglets.pack(fill="both", expand=True)
        f.onglets = onglets
        portrait.bind("<Button-1>", lambda _e: onglets.select(2))

        # --- Identité
        ident = ttk.Frame(onglets, style="Carte.TFrame", padding=16)
        onglets.add(ident, text=t("Identité"))
        v = {c: tk.StringVar() for c in ("nom", "espece", "bac", "sexe", "naissance", "arrivee", "origine", "signes")}
        champs = ((t("Nom"), "nom", "entree"), (t("Espèce"), "espece", "especes"), (t("Bac"), "bac", "bacs"),
                  (t("Sexe"), "sexe", "sexe"), (t("Date de naissance (ou ponte)"), "naissance", "date"),
                  (t("Arrivée chez vous"), "arrivee", "date"), (t("Origine (éleveur, lot…)"), "origine", "entree"),
                  (t("Signes distinctifs"), "signes", "entree"))
        libelles_especes, especes = especes_affichees()
        sexes = {t("Inconnu"): "Inconnu", t("Mâle"): "Mâle", t("Femelle"): "Femelle"}     # libellé -> valeur stockée
        aides = {}
        for rang, (texte, champ, genre) in enumerate(champs):
            ttk.Label(ident, text=texte, style="Carte.TLabel").grid(row=rang, column=0, sticky="w", pady=5, padx=(0, 14))
            if genre == "especes":
                w = ttk.Combobox(ident, textvariable=v[champ], values=libelles_especes, width=34)
            elif genre == "bacs":
                w = ttk.Combobox(ident, textvariable=v[champ], values=[b["nom"] for b in self.cfg["bacs"]], width=34,
                                 state="readonly")
            elif genre == "sexe":
                w = ttk.Combobox(ident, textvariable=v[champ], values=list(sexes), state="readonly", width=34)
            else:
                w = ttk.Entry(ident, textvariable=v[champ], width=60 if champ in ("origine", "signes") else 36)
            w.grid(row=rang, column=1, sticky="w", pady=5)
            if genre == "date":
                aides[champ] = ttk.Label(ident, style="Discret.TLabel")
                aides[champ].grid(row=rang, column=2, sticky="w", padx=(12, 0))
        ttk.Label(ident, text=t("Notes"), style="Carte.TLabel").grid(row=len(champs), column=0, sticky="nw", pady=5)
        notes = tk.Text(ident, height=7, width=72, bg=FOND, fg=TEXTE, insertbackground=TEXTE, relief="flat",
                        font=PT(10), wrap="word", padx=8, pady=6)
        notes.grid(row=len(champs), column=1, columnspan=2, sticky="nsew", pady=5)
        ident.columnconfigure(2, weight=1)
        ident.rowconfigure(len(champs), weight=1)
        bas = tk.Frame(ident, bg=CARTE)
        bas.grid(row=len(champs) + 1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(bas, text=t("Dates : 15/03/2019, 03/2019 ou simplement 2019 si l'année seule est connue."),
                  style="Discret.TLabel").pack(side="left")
        statut = ttk.Label(bas, style="Carte.TLabel", foreground=VERT)
        btn_enr = ttk.Button(bas, text=t("Enregistrer"), style="Vert.TButton")
        btn_enr.pack(side="right")
        statut.pack(side="right", padx=12)

        def remplir():
            fi = self.registre.copie_fiches()[cle]
            for c in ("nom", "origine", "signes"):
                v[c].set(fi.get(c, ""))
            v["espece"].set(t(fi["espece"]) if fi.get("espece") else "")
            bac = self._bac(fi.get("bac"))
            v["bac"].set(bac["nom"] if bac else "")
            v["sexe"].set(t(fi.get("sexe") or "Inconnu"))
            v["naissance"].set(fiches.date_saisie(fi.get("naissance", "")))
            v["arrivee"].set(fiches.date_saisie(fi.get("arrivee", "")))
            notes.delete("1.0", "end")
            notes.insert("1.0", fi.get("notes", ""))
            notes.edit_modified(False)

        def lire():
            """Valeurs saisies, prêtes à enregistrer ; ValueError si une date est fausse."""
            valeurs = {c: v[c].get().strip() for c in ("nom", "espece", "origine", "signes")}
            valeurs["espece"] = especes.get(valeurs["espece"], valeurs["espece"])
            choisi = v["bac"].get()
            valeurs["bac"] = next((b["id"] for b in self.cfg["bacs"] if b["nom"] == choisi), None)
            if valeurs["bac"] is None:          # bac retiré depuis : on garde ce qui était enregistré
                valeurs["bac"] = (self.registre.fiches.get(cle) or {}).get("bac", "") if not choisi else ""
            sexe = sexes.get(v["sexe"].get(), v["sexe"].get())
            valeurs["sexe"] = "" if sexe == "Inconnu" else sexe
            for c, nom in (("naissance", t("naissance")), ("arrivee", t("arrivée"))):
                try:
                    valeurs[c] = fiches.lire_date_partielle(v[c].get())
                except ValueError as e:
                    raise ValueError(t("{champ} : {erreur}", champ=nom, erreur=e)) from None
            valeurs["notes"] = notes.get("1.0", "end").strip()
            return valeurs

        affiche = {}                            # valeurs du formulaire au dernier remplissage ou enregistrement

        def modifiee():
            try:
                return lire() != affiche
            except ValueError:
                return True

        def aide_dates(*_):
            for c, lbl in aides.items():
                try:
                    valeur = fiches.lire_date_partielle(v[c].get())
                    texte = (fiches.age_lisible(valeur) if c == "naissance" else
                             t("depuis {duree}", duree=fiches.age_lisible(valeur))) if valeur else t("jj/mm/aaaa, mm/aaaa ou aaaa")
                    lbl.configure(text=texte, foreground=AMBRE if valeur else DISCRET)
                except ValueError as e:
                    lbl.configure(text=str(e), foreground=ROUGE)
            statut.configure(text="")

        def enregistrer(_=None):
            try:
                valeurs = lire()
            except ValueError as e:
                messagebox.showerror(t("Fiche"), t("Pas enregistré — {erreur}", erreur=e), parent=f)
                return False
            if not valeurs["nom"]:
                messagebox.showerror(t("Fiche"), t("Le nom ne peut pas être vide."), parent=f)
                return False
            fi = self.registre.copie_fiches().get(cle)
            if fi is None:                      # fusionnée ou supprimée entre-temps
                self._fermer_fiche(cle)
                return False
            valeurs["provisoire"] = False
            self.registre.modifier(cle, **valeurs)
            notes.edit_modified(False)
            affiche.clear()
            affiche.update({k: v_ for k, v_ in valeurs.items() if k != "provisoire"})
            bandeau()
            self._maj_poissons()
            statut.configure(text=t("Enregistré ✓"))
            return True

        btn_enr.configure(command=enregistrer)
        for c in ("naissance", "arrivee"):
            v[c].trace_add("write", aide_dates)
        f.bind("<Control-s>", enregistrer)

        # --- Croissance
        cadre_croissance = ttk.Frame(onglets, style="Carte.TFrame", padding=12)
        onglets.add(cadre_croissance, text=t("Croissance"))
        self._panneau_mesures(cadre_croissance, cle, apres=lambda: (bandeau(), self._maj_poissons()))

        # --- Photos : album souvenir + vues apprises par la reconnaissance
        cadre_photos = ttk.Frame(onglets, style="Carte.TFrame", padding=12)
        onglets.add(cadre_photos, text=t("Photos"))
        barre = tk.Frame(cadre_photos, bg=CARTE)
        barre.pack(fill="x")
        v_reco = tk.BooleanVar(value=False)
        btn_ajout = ttk.Button(barre, text="➕  " + t("Ajouter des photos…"), style="Vert.TButton")
        btn_ajout.pack(side="left")
        ttk.Checkbutton(barre, text=t("Les apprendre aussi pour la reconnaissance (poisson de profil, bien visible)"),
                        variable=v_reco).pack(side="left", padx=12)
        ttk.Button(barre, text="📁  " + t("Dossier"), command=lambda: os.startfile(self.registre.dossier / cle)
                   if (self.registre.dossier / cle).exists() else None).pack(side="right")
        defile = tk.Canvas(cadre_photos, bg=CARTE, highlightthickness=0)
        barre_def = ttk.Scrollbar(cadre_photos, orient="vertical", command=defile.yview)
        defile.configure(yscrollcommand=barre_def.set)
        barre_def.pack(side="right", fill="y", pady=(10, 0))
        defile.pack(fill="both", expand=True, pady=(10, 0))
        grille = tk.Frame(defile, bg=CARTE)
        defile.create_window(0, 0, window=grille, anchor="nw")
        grille.bind("<Configure>", lambda _e: defile.configure(scrollregion=defile.bbox("all")))
        defile.bind("<MouseWheel>", lambda e: defile.yview_scroll(-e.delta // 120, "units"))
        etat_photos = {"tour": 0}

        def menu_photo(ev, chemin, album):
            m = tk.Menu(f, tearoff=0)
            m.add_command(label=t("Ouvrir"), command=lambda: os.startfile(chemin))
            relatif = f"{ALBUM}/{chemin.name}" if album else chemin.name
            m.add_command(label=t("Choisir comme photo de profil"),
                          command=lambda: (self.registre.modifier(cle, profil=relatif), bandeau(), self._maj_poissons()))
            m.add_separator()
            if album:
                m.add_command(label=t("Retirer de l'album…"), command=lambda: retirer(chemin, True))
            else:
                m.add_command(label=t("Retirer de la reconnaissance (mauvaise vue)…"), command=lambda: retirer(chemin, False))
            m.tk_popup(ev.x_root, ev.y_root)

        def retirer(chemin, album):
            question = t("Retirer cette photo de l'album ? Elle est effacée du disque.") if album else \
                t("Retirer cette vue ? Frétille ne s'en servira plus pour reconnaître ce poisson.")
            if not messagebox.askyesno(t("Photos"), question, parent=f):
                return
            if album:
                self.registre.retirer_album(cle, chemin.name)
            else:
                self.registre.retirer_vue(cle, chemin.name)
            bandeau()
            photos()
            self._maj_poissons()

        def photos():
            """Reconstruit les deux grilles ; les vignettes arrivent par paquets pour garder la main."""
            etat_photos["tour"] += 1
            tour = etat_photos["tour"]
            for w in grille.winfo_children():
                w.destroy()
            album = self.registre.album(cle)
            d = self.registre.dossier / cle
            vues = sorted(d.glob("*.jpg"), reverse=True) if d.is_dir() else []
            a_charger = []
            rang = 0
            for titre, liste, est_album, vide in (
                    (t("ALBUM  ({n})", n=len(album)), album, True,
                     t("Aucune photo souvenir. « Ajouter des photos… » pour en mettre.")),
                    (t("VUES APPRISES PAR LA RECONNAISSANCE  ({n})", n=len(vues)), vues, False,
                     t("Pas encore de vue : elles s'ajoutent à chaque visite confirmée."))):
                ttk.Label(grille, text=titre, style="Titre.TLabel").grid(row=rang, column=0, columnspan=6, sticky="w",
                                                                          pady=(10 if rang else 0, 6))
                rang += 1
                if not liste:
                    ttk.Label(grille, text=vide, style="Discret.TLabel").grid(row=rang, column=0, columnspan=6, sticky="w")
                    rang += 1
                for k, chemin in enumerate(liste):
                    case = tk.Frame(grille, bg=CARTE)
                    case.grid(row=rang + k // 6, column=k % 6, padx=4, pady=4, sticky="n")
                    img = tk.Label(case, bg=FOND, width=20, height=8, cursor="hand2")
                    img.pack()
                    legende = chemin.name[:10] if chemin.name[:4].isdigit() else ""
                    if legende:
                        legende = fiches.date_lisible(legende[:10])
                    tk.Label(case, text=legende, bg=CARTE, fg=DISCRET, font=PT(8)).pack()
                    img.bind("<Double-1>", lambda _e, c=chemin: os.startfile(c))
                    img.bind("<Button-3>", lambda e, c=chemin, a=est_album: menu_photo(e, c, a))
                    for w in (case, img):
                        w.bind("<MouseWheel>", lambda e: defile.yview_scroll(-e.delta // 120, "units"))
                    a_charger.append((img, chemin))
                rang += (len(liste) + 5) // 6

            def paquet(debut=0):
                if tour != etat_photos["tour"] or not f.winfo_exists():
                    return
                for img, chemin in a_charger[debut:debut + 6]:
                    vignette = self._vignette(chemin, (140, 105))
                    if vignette and img.winfo_exists():
                        img.configure(image=vignette, width=140, height=105)
                        img.image = vignette
                if debut + 6 < len(a_charger):
                    f.after(1, paquet, debut + 6)
            paquet()

        def ajouter_photos():
            chemins = filedialog.askopenfilenames(parent=f, title=t("Photos de {nom}", nom=self.registre.fiches[cle]["nom"]),
                                                  filetypes=[(t("Photos"), "*.jpg *.jpeg *.png")])
            if not chemins:
                return
            f.configure(cursor="watch")
            f.update_idletasks()
            try:
                ecrits = self.registre.ajouter_album(cle, chemins)
            finally:
                f.configure(cursor="")
            if v_reco.get():
                if self.modeles_prets:
                    self._importer_photos(cle, chemins)
                else:
                    messagebox.showinfo(t("Photos"), t("Les modèles se chargent encore : les photos sont dans l'album, "
                                                       "réessayez la reconnaissance dans un moment."), parent=f)
            if len(ecrits) < len(chemins):
                messagebox.showwarning(t("Photos"), tn("{n} fichier illisible ignoré.", "{n} fichiers illisibles ignorés.",
                                                       len(chemins) - len(ecrits)), parent=f)
            bandeau()
            photos()
            self._maj_poissons()

        btn_ajout.configure(command=ajouter_photos)

        # --- Carnet : soins, vétérinaire, ponte…
        cadre_carnet = ttk.Frame(onglets, style="Carte.TFrame", padding=12)
        onglets.add(cadre_carnet, text=t("Carnet"))
        saisie = tk.Frame(cadre_carnet, bg=CARTE)
        saisie.pack(fill="x")
        v_date = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        types_carnet = {t(x): x for x in fiches.TYPES_CARNET}      # libellé traduit -> type stocké
        v_type, v_texte = tk.StringVar(value=t(fiches.TYPES_CARNET[0])), tk.StringVar()
        for col, texte in enumerate((t("Date"), t("Type"), t("Ce qui s'est passé"))):
            ttk.Label(saisie, text=texte, style="Carte.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 10))
        e_date = ttk.Entry(saisie, textvariable=v_date, width=12)
        e_date.grid(row=1, column=0, padx=(0, 10))
        ttk.Combobox(saisie, textvariable=v_type, values=list(types_carnet), width=16).grid(row=1, column=1, padx=(0, 10))
        e_texte = ttk.Entry(saisie, textvariable=v_texte, width=60)
        e_texte.grid(row=1, column=2, padx=(0, 10))
        colonnes = (("date", t("DATE"), 100), ("type", t("TYPE"), 130), ("texte", t("NOTE"), 640))
        liste = ttk.Treeview(cadre_carnet, columns=[c[0] for c in colonnes], show="headings", selectmode="browse")
        liste.configure(style="Liste.Treeview")
        for col, titre, larg in colonnes:
            liste.heading(col, text=titre, anchor="w")
            liste.column(col, width=larg, anchor="w", stretch=col == "texte")
        liste.pack(fill="both", expand=True, pady=(12, 0))
        ttk.Label(cadre_carnet, text=t("Traitement, quarantaine, blessure, ponte, changement de bac… "
                                       "Clic droit sur une ligne pour la retirer."), style="Discret.TLabel").pack(anchor="w", pady=(6, 0))

        notes_affichees = {}                    # ligne -> note montrée (le téléphone peut en ajouter entre-temps)

        def carnet():
            liste.delete(*liste.get_children())
            notes_affichees.clear()
            for rang, n in reversed(list(enumerate(self.registre.carnet(cle)))):
                notes_affichees[str(rang)] = n
                liste.insert("", "end", iid=str(rang), values=(fiches.date_lisible(n['date']),
                                                               t(n["type"]) if n.get("type") else "", n["texte"]))

        def noter(_=None):
            try:
                jour = datetime.strptime(v_date.get().strip(), "%d/%m/%Y").date()
                if jour > datetime.now().date():
                    raise ValueError(t("date dans le futur"))
                genre = v_type.get().strip()
                self.registre.ajouter_note(cle, jour.isoformat(), types_carnet.get(genre, genre) or "Note", v_texte.get())
            except KeyError:
                self._fermer_fiche(cle)
                return
            except ValueError as e:
                messagebox.showerror(t("Carnet"), t("Note refusée : {erreur}\nDate au format 08/11/2026.", erreur=e), parent=f)
                return
            v_texte.set("")
            carnet()

        def menu_note(ev):
            rang = liste.identify_row(ev.y)
            if not rang:
                return
            liste.selection_set(rang)
            m = tk.Menu(f, tearoff=0)
            m.add_command(label=t("Retirer cette note"), command=lambda: (self.registre.retirer_note(cle, int(rang), notes_affichees.get(rang)), carnet()))
            m.tk_popup(ev.x_root, ev.y_root)

        ttk.Button(saisie, text=t("Noter"), style="Vert.TButton", command=noter).grid(row=1, column=3)
        for w in (e_date, e_texte):
            w.bind("<Return>", noter)
        liste.bind("<Button-3>", menu_note)

        # --- fermeture : ne pas perdre une identité modifiée
        def fermer():
            if cle in self.registre.fiches and modifiee():
                choix = messagebox.askyesnocancel(t("Fiche"), t("Enregistrer les modifications de l'identité ?"), parent=f)
                if choix is None or (choix and not enregistrer()):
                    return
            self.fenetres_fiche.pop(cle, None)
            f.destroy()

        f.protocol("WM_DELETE_WINDOW", fermer)
        f.fermer = fermer                       # appelé aussi en quittant Broutille
        f.bind("<Escape>", lambda _e: fermer())
        onglets.bind("<<NotebookTabChanged>>",
                     lambda _e: photos() if onglets.index("current") == 2 else None)
        remplir()
        aide_dates()
        bandeau()
        carnet()
        affiche.update(lire())
        onglets.select(onglet)


    def _panneau_mesures(self, f, cle, apres=None):
        """Croissance : saisie (date, longueur, poids, remarque), courbes et liste. Les mesures « vidéo »
        viennent des bacs étalonnés ; une mesure à la main prime le même jour."""
        saisie = tk.Frame(f, bg=CARTE)
        saisie.pack(fill="x")
        v_date = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        v_cm, v_g, v_note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        entrees = []
        for col, (texte, var, larg) in enumerate(((t("Date"), v_date, 12), (t("Longueur (cm)"), v_cm, 10),
                                                   (t("Poids (g)"), v_g, 10), (t("Remarque"), v_note, 30))):
            ttk.Label(saisie, text=texte, style="Carte.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 10))
            e = ttk.Entry(saisie, textvariable=var, width=larg)
            e.grid(row=1, column=col, sticky="w", padx=(0, 10))
            entrees.append(e)
        graphes = tk.Frame(f, bg=CARTE)
        graphes.pack(fill="x", pady=(12, 6))
        g_cm, g_g = tk.Label(graphes, bg=CARTE), tk.Label(graphes, bg=CARTE)
        g_cm.pack(side="left")
        g_g.pack(side="left", padx=(10, 0))
        colonnes = (("date", t("DATE"), 100), ("cm", t("LONGUEUR"), 90), ("g", t("POIDS"), 90), ("source", t("SOURCE"), 90),
                    ("note", t("REMARQUE"), 320))
        liste = ttk.Treeview(f, columns=[c[0] for c in colonnes], show="headings", height=7, selectmode="browse",
                             style="Liste.Treeview")
        for col, titre, larg in colonnes:
            liste.heading(col, text=titre, anchor="w")
            liste.column(col, width=larg, anchor="w")
        liste.pack(fill="both", expand=True)
        ttk.Label(f, text=t("Mesures « vidéo » : bac étalonné, longueur du poisson reconnu, une par jour. "
                            "Clic droit sur une mesure pour la retirer."), style="Discret.TLabel").pack(anchor="w", pady=(6, 0))
        lignes = {}

        def rafraichir():
            mesures = self.registre.mesures(cle)
            courbe_cm = [(m["date"], m["cm"]) for m in mesures if m.get("cm") and m.get("source") == "main"] or \
                [(m["date"], m["cm"]) for m in mesures if m.get("cm")]
            g_cm.image = ImageTk.PhotoImage(graphiques.courbe(courbe_cm, "cm", t("Longueur"), (450, 240)))
            g_cm.configure(image=g_cm.image)
            g_g.image = ImageTk.PhotoImage(graphiques.courbe([(m["date"], m["g"]) for m in mesures if m.get("g")], "g",
                                                             t("Poids"), (450, 240), graphiques.AMBRE))
            g_g.configure(image=g_g.image)
            liste.delete(*liste.get_children())
            lignes.clear()
            for m in reversed(mesures):
                iid = liste.insert("", "end", values=(fiches.date_lisible(m['date']),
                                                      t("{v} cm", v=nombre(m["cm"])) if m.get("cm") else "",
                                                      t("{v} g", v=nombre(m["g"])) if m.get("g") else "",
                                                      t("vidéo") if m.get("source") == "video" else t("à la main"),
                                                      m.get("note", "")))
                lignes[iid] = m
            if apres:
                apres()

        def ajouter(_=None):
            try:
                jour = datetime.strptime(v_date.get().strip(), "%d/%m/%Y").date()
                if jour > date.today():
                    raise ValueError(t("date dans le futur"))
                cm = v_cm.get().replace(",", ".").replace(" ", "").lower().removesuffix("cm") or None
                g = v_g.get().replace(",", ".").replace(" ", "").lower().removesuffix("g") or None
                self.registre.ajouter_mesure(cle, jour.isoformat(), cm=cm, grammes=g, source="main", note=v_note.get())
            except KeyError:
                if f.winfo_toplevel() is not self.root:
                    f.winfo_toplevel().destroy()
                return
            except ValueError as e:
                messagebox.showerror(t("Mesure"), t("Mesure refusée : {erreur}\nDate au format 10/11/2026, longueur en cm, "
                                                    "poids en grammes.", erreur=e), parent=f.winfo_toplevel())
                return
            v_cm.set("")
            v_g.set("")
            v_note.set("")
            rafraichir()

        def menu(ev):
            iid = liste.identify_row(ev.y)
            m = lignes.get(iid)
            if not m:
                return
            liste.selection_set(iid)
            mm = tk.Menu(f, tearoff=0)
            mm.add_command(label=t("Retirer cette mesure"),
                           command=lambda: (self.registre.retirer_mesure(cle, m["date"], m.get("source", "main")), rafraichir()))
            mm.tk_popup(ev.x_root, ev.y_root)

        ttk.Button(saisie, text=t("Ajouter"), style="Vert.TButton", command=ajouter).grid(row=1, column=4, sticky="w")
        for e in entrees:
            e.bind("<Return>", ajouter)
        liste.bind("<Button-3>", menu)
        rafraichir()

    # -------------------------------------------------------------- réglages d'un bac
    def dialogue_bac(self, bac=None):
        """Crée ou modifie un bac : nom, genre, caméra, poissons attendus, température, outils sur l'image
        (cadrage, ligne d'eau, étalonnage des tailles)."""
        nouveau = bac is None
        bac = dict(bac or {"id": f"b{int(time.time() * 1000):x}", "nom": t("Bac {n}", n=len(self.cfg["bacs"]) + 1),
                           "type": "aquarium", "source": "0", "nombre": 0, "reconnaissance": True, "actif": True})
        f = tk.Toplevel(self.root, bg=CARTE, padx=20, pady=16)
        f.title(t("Nouveau bac") if nouveau else t("Bac « {bac} »", bac=bac["nom"]))
        f.transient(self.root)
        f.grab_set()
        types_bac = {t(libelle): genre for genre, libelle in TYPES_BAC.items()}     # libellé traduit -> type stocké
        v_nom = tk.StringVar(value=bac["nom"])
        v_type = tk.StringVar(value=t(TYPES_BAC.get(bac.get("type"), "Aquarium")))
        v_source = tk.StringVar(value=libelle_source(bac.get("source", "0")))
        v_nombre = tk.StringVar(value=str(bac.get("nombre") or ""))
        v_tmin = tk.StringVar(value=nombre(bac.get("temp_min")) if bac.get("temp_min") is not None else "")
        v_tmax = tk.StringVar(value=nombre(bac.get("temp_max")) if bac.get("temp_max") is not None else "")
        v_reco = tk.BooleanVar(value=bac.get("reconnaissance", True))

        def ligne(rang, texte, widget, aide=""):
            ttk.Label(f, text=texte, style="Carte.TLabel").grid(row=rang, column=0, sticky="w", pady=6, padx=(0, 14))
            widget.grid(row=rang, column=1, sticky="w")
            if aide:
                ttk.Label(f, text=aide, style="Discret.TLabel").grid(row=rang, column=2, sticky="w", padx=(10, 0))

        ligne(0, t("Nom"), ttk.Entry(f, textvariable=v_nom, width=34))
        ligne(1, t("Genre"), ttk.Combobox(f, textvariable=v_type, values=list(types_bac), state="readonly", width=32),
              t("Bassin : prédateurs surveillés, vue de dessus"))
        cadre_source = tk.Frame(f, bg=CARTE)
        ttk.Combobox(cadre_source, textvariable=v_source, values=[libelle_source(i) for i in range(4)], width=34).pack(side="left")
        ttk.Button(cadre_source, text="📷", width=3, command=lambda: self._choisir_webcam(v_source, f)).pack(side="left", padx=(4, 0))
        ttk.Button(cadre_source, text="🪟", width=3, command=lambda: self._choisir_fenetre(v_source, f)).pack(side="left", padx=(4, 0))
        ttk.Button(cadre_source, text="🎞", width=3,
                   command=lambda: v_source.set(filedialog.askopenfilename(parent=f, filetypes=[(t("Vidéos"), "*.mp4 *.avi *.mkv *.mov")])
                                                or v_source.get())).pack(side="left", padx=(4, 0))
        ligne(2, t("Caméra"), cadre_source, t("webcam (📷), fenêtre ouverte (🪟), vidéo (🎞) ou rtsp://…"))
        ligne(3, t("Poissons dans le bac"), ttk.Entry(f, textvariable=v_nombre, width=8),
              t("laisser vide : Frétille apprend le nombre habituel"))
        temp = tk.Frame(f, bg=CARTE)
        ttk.Entry(temp, textvariable=v_tmin, width=6).pack(side="left")
        ttk.Label(temp, text="  " + t("à") + "  ", style="Carte.TLabel").pack(side="left")
        ttk.Entry(temp, textvariable=v_tmax, width=6).pack(side="left")
        ttk.Label(temp, text="  °C", style="Carte.TLabel").pack(side="left")
        ligne(4, t("Température conseillée"), temp, t("pour le carnet de l'eau"))
        ttk.Checkbutton(f, text=t("Reconnaître les poissons un par un (koï, discus… à motifs uniques)"), variable=v_reco
                        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 4))

        etat_outils = tk.Label(f, bg=CARTE, fg=DISCRET, font=PT(9), justify="left", anchor="w")
        etat_outils.grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))

        def montrer_outils():
            morceaux = [t("cadrage : oui") if bac.get("zone") else t("cadrage : toute l'image"),
                        t("ligne d'eau : à {pourcent} du haut", pourcent=pourcent(bac["surface"]))
                        if bac.get("surface") is not None else t("ligne d'eau : non réglée"),
                        t("étalonnage : {v} cm pour 100 px", v=nombre(bac["cm_par_px"] * 100, 2)) if bac.get("cm_par_px")
                        else t("étalonnage : non fait (tailles non mesurées)")]
            etat_outils.configure(text="   ·   ".join(morceaux))

        def source_actuelle():
            bac["source"] = source_depuis_saisie(v_source.get())
            return bac["source"]

        outils_ = tk.Frame(f, bg=CARTE)
        outils_.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(outils_, text="⛶  " + t("Cadrer"),
                   command=lambda: self._outil_image(bac, source_actuelle(), "cadrer", montrer_outils, f)).pack(side="left")
        ttk.Button(outils_, text="〰  " + t("Ligne d'eau"), command=lambda: self._outil_image(bac, source_actuelle(), "surface",
                                                                                            montrer_outils, f)).pack(side="left", padx=(8, 0))
        ttk.Button(outils_, text="📏  " + t("Étalonner les tailles"), command=lambda: self._outil_image(bac, source_actuelle(),
                                                                                              "etalonner", montrer_outils, f)
                   ).pack(side="left", padx=(8, 0))
        montrer_outils()

        def enregistrer():
            if not v_nom.get().strip():
                messagebox.showerror(t("Bac"), t("Donnez un nom au bac."), parent=f)
                return
            try:
                nombre = int(v_nombre.get() or 0)
                tmin = float(v_tmin.get().replace(",", ".")) if v_tmin.get().strip() else None
                tmax = float(v_tmax.get().replace(",", ".")) if v_tmax.get().strip() else None
                if nombre < 0 or (tmin is not None and tmax is not None and tmin > tmax):
                    raise ValueError
            except ValueError:
                messagebox.showerror(t("Bac"), t("Nombre de poissons ou températures illisibles."), parent=f)
                return
            genre = types_bac[v_type.get()]
            bac.update(nom=v_nom.get().strip(), type=genre, source=source_depuis_saisie(v_source.get()), nombre=nombre,
                       temp_min=tmin, temp_max=tmax, reconnaissance=v_reco.get())
            self.cfg["bacs"] = [b for b in self.cfg["bacs"] if b["id"] != bac["id"]] + [bac] if nouveau else \
                [bac if b["id"] == bac["id"] else b for b in self.cfg["bacs"]]
            self._ecrire_config()
            f.destroy()
            if nouveau:
                self.bac_courant = bac["id"]
            elif bac["id"] in self.surveillances:
                self.lbl_etat.configure(text=t("Réglages du bac enregistrés : arrêtez et relancez sa surveillance pour les appliquer"))
            self._maj_barre_bacs()
            self._maj_etat_bac()
            if bac["id"] not in self.surveillances:
                self._afficher_vide()

        def supprimer():
            if messagebox.askyesno(t("Bac"), t("Retirer le bac « {bac} » ? Ses mesures et photos restent sur le disque.",
                                               bac=bac["nom"]), parent=f):
                self.arreter(bac["id"])
                self.cfg["bacs"] = [b for b in self.cfg["bacs"] if b["id"] != bac["id"]]
                self._ecrire_config()
                f.destroy()
                self.bac_courant = self.cfg["bacs"][0]["id"] if self.cfg["bacs"] else None
                self._maj_barre_bacs()
                self._maj_etat_bac()
                self._afficher_vide()

        bas = tk.Frame(f, bg=CARTE)
        bas.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        if not nouveau:
            ttk.Button(bas, text=t("Retirer ce bac"), command=supprimer).pack(side="left")
        ttk.Button(bas, text=t("Enregistrer"), style="Vert.TButton", command=enregistrer).pack(side="right")

    def _choisir_fenetre(self, variable, parent):
        f = tk.Toplevel(parent, bg=CARTE, padx=16, pady=14)
        f.title(t("Filmer une fenêtre ouverte"))
        f.transient(parent)
        ttk.Label(f, text=t("La fenêtre doit rester ouverte, pas réduite. Elle peut être cachée par d'autres."),
                  style="Discret.TLabel").pack(anchor="w", pady=(0, 8))
        liste = tk.Listbox(f, width=64, height=12, bg=FOND, fg=TEXTE, selectbackground=ACCENT, relief="flat", font=PT(10))
        liste.pack(fill="both", expand=True)
        titres = sorted({titre for titre, _h in sources.lister_fenetres() if titre not in (self.root.title(), f.title())})
        for titre in titres:
            liste.insert("end", titre)

        def valider(_=None):
            if liste.curselection():
                variable.set(libelle_source(sources.PREFIXE_FENETRE + titres[liste.curselection()[0]]))
                f.destroy()

        liste.bind("<Double-1>", valider)
        ttk.Button(f, text=t("Choisir"), style="Vert.TButton", command=valider).pack(anchor="e", pady=(10, 0))

    def _choisir_webcam(self, variable, parent):
        """Essaie chaque webcam (dans un fil) et montre une image de chacune : on clique sur la
        bonne. Les numéros ne disent pas laquelle est celle du portable et laquelle est en USB."""
        f = tk.Toplevel(parent, bg=CARTE, padx=16, pady=14)
        f.title(t("Choisir une webcam"))
        f.transient(parent)
        ttk.Label(f, text=t("Recherche des webcams… (jusqu'à 30 secondes)"), style="Carte.TLabel").pack(padx=40, pady=30)

        def travail():
            try:
                trouvees = sources.lister_webcams()
            except Exception:                      # noqa: BLE001 - pilote de caméra capricieux
                trouvees = []
            self.messages.put(("webcams", (f, variable, trouvees)))
        threading.Thread(target=travail, daemon=True).start()

    def _montrer_webcams(self, f, variable, trouvees):
        if not f.winfo_exists():
            return
        for enfant in f.winfo_children():
            enfant.destroy()
        if not trouvees:
            ttk.Label(f, text=t("Aucune webcam ne donne d'image. Vérifiez qu'elle est branchée et qu'aucun autre "
                                "programme ne l'utilise (Caméra, Teams, OBS…)."), style="Carte.TLabel", wraplength=420,
                      justify="left").pack(padx=10, pady=20)
            return
        ttk.Label(f, text=t("Cliquez sur l'image de la bonne caméra."), style="Discret.TLabel").pack(anchor="w", pady=(0, 8))
        rangee = tk.Frame(f, bg=CARTE)
        rangee.pack()
        f.vignettes = []

        def choisir(numero):
            variable.set(libelle_source(numero))
            f.destroy()

        for rang, (numero, image) in enumerate(trouvees):
            img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            img.thumbnail((300, 200))
            vignette = ImageTk.PhotoImage(img)
            f.vignettes.append(vignette)
            case = tk.Frame(rangee, bg=CARTE)
            case.grid(row=rang // 3 * 2, column=rang % 3, padx=6, pady=6)
            bouton = tk.Label(case, image=vignette, bg=CARTE, cursor="hand2")
            bouton.pack()
            legende = tk.Label(case, text=f"{libelle_source(numero)}  ·  {image.shape[1]}×{image.shape[0]}", bg=CARTE, fg=TEXTE,
                               font=PT(10), cursor="hand2")
            legende.pack()
            for w in (bouton, legende):
                w.bind("<Button-1>", lambda _e, n=numero: choisir(n))

    def _outil_image(self, bac, source, outil, apres, parent):
        """Lit une image de la caméra du bac (dans un fil), puis ouvre l'outil sur elle."""
        s = self.surveillances.get(bac["id"])
        if s is not None and s.image_brute is not None:     # caméra déjà ouverte : son image entière
            self._fenetre_outil(bac, s.image_brute.copy(), outil, apres, parent)
            return
        self.lbl_etat.configure(text=t("Lecture d'une image de la caméra…"))

        def travail():
            image = None
            try:
                camera, image = sources.ouvrir_source(sources.source_depuis_texte(str(source)))
                if camera is not None:
                    camera.release()
            except Exception as e:
                self.messages.put(("message", t("Caméra illisible : {erreur}", erreur=e)))
            self.messages.put(("outil", (bac, image, outil, apres, parent)))
        threading.Thread(target=travail, daemon=True).start()

    def _fenetre_outil(self, bac, image, outil, apres, parent):
        """cadrer : rectangle à garder ; surface : clic sur la ligne d'eau ; etalonner : trait d'une
        longueur connue (une règle posée contre la vitre, la largeur d'une pierre…)."""
        if outil != "cadrer" and bac.get("zone"):
            z = bac["zone"]
            h0, w0 = image.shape[:2]
            image = image[int(z[1] * h0):int(z[3] * h0), int(z[0] * w0):int(z[2] * w0)]
        h, w = image.shape[:2]
        e = min(1100 / w, 680 / h, 1.0)
        f = tk.Toplevel(parent, bg=CARTE, padx=12, pady=12)
        f.title({"cadrer": t("Cadrer : tracez la zone du bac à la souris"),
                 "surface": t("Ligne d'eau : cliquez sur la surface de l'eau"),
                 "etalonner": t("Étalonner : tracez un trait de longueur connue")}[outil])
        f.transient(parent)
        photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(cv2.resize(image, (int(w * e), int(h * e))), cv2.COLOR_BGR2RGB)))
        c = tk.Canvas(f, width=int(w * e), height=int(h * e), highlightthickness=0)
        c.pack()
        c.create_image(0, 0, anchor="nw", image=photo)
        c.image = photo
        depart = [0, 0]
        clique = [False]
        forme = c.create_line(0, 0, 0, 0, fill=AMBRE, width=3) if outil != "cadrer" else \
            c.create_rectangle(*(v * d for v, d in zip(bac.get("zone") or [0, 0, 1, 1], (w * e, h * e, w * e, h * e))),
                               outline=AMBRE, width=3)
        if outil == "surface" and bac.get("surface") is not None:
            y = bac["surface"] * h * e
            c.coords(forme, 0, y, w * e, y)
        bas = tk.Frame(f, bg=CARTE)
        bas.pack(fill="x", pady=(10, 0))
        v_cm = tk.StringVar()
        if outil == "etalonner":
            ttk.Label(bas, text=t("Longueur réelle du trait (cm)"), style="Carte.TLabel").pack(side="left")
            ttk.Entry(bas, textvariable=v_cm, width=8).pack(side="left", padx=(8, 0))
            ttk.Label(bas, text="  " + t("Tracez-le à la distance où nagent les poissons."), style="Discret.TLabel").pack(side="left")

        def appui(ev):
            depart[:] = [ev.x, ev.y]
            clique[0] = True
            if outil == "surface":
                c.coords(forme, 0, ev.y, w * e, ev.y)

        def glisse(ev):
            if outil == "cadrer":
                c.coords(forme, depart[0], depart[1], ev.x, ev.y)
            elif outil == "etalonner":
                c.coords(forme, depart[0], depart[1], ev.x, ev.y)
            else:
                c.coords(forme, 0, ev.y, w * e, ev.y)

        c.bind("<ButtonPress-1>", appui)
        c.bind("<B1-Motion>", glisse)

        def valider():
            try:
                if outil == "cadrer":
                    x0, y0, x1, y1 = c.coords(forme)
                    x0, x1 = sorted((max(0, x0), min(w * e, x1)))
                    y0, y1 = sorted((max(0, y0), min(h * e, y1)))
                    if x1 - x0 < 64 or y1 - y0 < 64:
                        raise ValueError(t("zone trop petite"))
                    zone = [x0 / (w * e), y0 / (h * e), x1 / (w * e), y1 / (h * e)]
                    if clique[0] and zone != (bac.get("zone") or [0, 0, 1, 1]):
                        bac["zone"] = zone
                        bac["cm_par_px"] = None     # autre cadre : étalonnage et ligne d'eau à refaire
                        bac["surface"] = None
                elif outil == "surface":
                    if not clique[0] and bac.get("surface") is None:
                        raise ValueError(t("cliquez sur la surface de l'eau"))
                    y = c.coords(forme)[1]
                    bac["surface"] = max(0.0, min(0.9, y / (h * e)))
                else:
                    if not clique[0]:
                        raise ValueError(t("tracez d'abord le trait"))
                    x0, y0, x1, y1 = c.coords(forme)
                    cm = float(v_cm.get().replace(",", "."))
                    bac["cm_par_px"] = etalonner(x0 / e, y0 / e, x1 / e, y1 / e, cm)
            except ValueError as err:
                messagebox.showerror(t("Bac"), t("Impossible : {erreur}", erreur=err), parent=f)
                return
            apres()
            f.destroy()

        def effacer():
            bac.update({"cadrer": {"zone": None, "cm_par_px": None}, "surface": {"surface": None},
                        "etalonner": {"cm_par_px": None}}[outil])
            apres()
            f.destroy()

        ttk.Button(bas, text=t("Valider"), style="Vert.TButton", command=valider).pack(side="right")
        ttk.Button(bas, text=t("Effacer"), command=effacer).pack(side="right", padx=(0, 8))

    # -------------------------------------------------------------- santé
    def fenetre_sante(self):
        bac = self._bac(self.bac_courant)
        if not bac:
            return
        f = tk.Toplevel(self.root, bg=CARTE, padx=16, pady=14)
        f.title(t("Santé — {bac}", bac=bac["nom"]))
        f.transient(self.root)
        aujourd_hui = date.today()
        try:
            lignes = lire_mesures(self.dossier, bac["id"], aujourd_hui)
            hist = sante.historique_jours(lambda i, j: lire_mesures(self.dossier, i, j), bac["id"], aujourd_hui)
        except (OSError, FichierOccupe) as e:
            messagebox.showerror(t("Santé"), t("Mesures illisibles : {erreur}", erreur=e), parent=f)
            f.destroy()
            return
        tout_hist = [l for ls in hist.values() for l in ls]
        b = sante.bilan(bac, lignes, hist)
        etat = b["etat"]
        tk.Label(f, text=t("{bac} : {etat}", bac=bac["nom"], etat=t(LIBELLE_ETAT[etat])), bg=CARTE, fg=COULEUR_ETAT[etat],
                 font=PT(18, True)).pack(anchor="w")
        tk.Label(f, text="\n".join(f"• {r}" for r in b["raisons"]), bg=CARTE, fg=TEXTE, justify="left",
                 font=PT(10)).pack(anchor="w", pady=(4, 10))
        for cle, titre in (("activite", t("Activité par heure")), ("nb_max", t("Poissons vus par heure")),
                           ("surface", t("Part des poissons sous la surface"))):
            if cle == "surface" and (bac.get("surface") is None or bac.get("type") == "bassin"):
                continue
            img = graphiques.journee(sante.serie_horaire(lignes, cle), sante.serie_horaire(tout_hist, cle),
                                     t("{titre} — aujourd'hui (barres) et habitude des 7 derniers jours (trait)", titre=titre),
                                     (920, 200))
            lbl = tk.Label(f, bg=CARTE)
            lbl.image = ImageTk.PhotoImage(img)
            lbl.configure(image=lbl.image)
            lbl.pack(pady=(0, 8))
        if not lignes:
            ttk.Label(f, text=t("Pas encore de mesure aujourd'hui : démarrez la surveillance du bac."),
                      style="Discret.TLabel").pack(anchor="w")

    # -------------------------------------------------------------- eau
    def fenetre_eau(self):
        bac = self._bac(self.bac_courant)
        if not bac:
            return
        f = tk.Toplevel(self.root, bg=CARTE, padx=16, pady=14)
        f.title(t("Carnet de l'eau — {bac}", bac=bac["nom"]))
        f.transient(self.root)
        saisie = tk.Frame(f, bg=CARTE)
        saisie.pack(fill="x")
        v_date = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        champs = [("date", t("Date"), v_date, 11)] + [(cle, t(nom) + (f" ({u})" if u else ""), tk.StringVar(), 7)
                                                       for cle, nom, u, *_ in eau.PARAMETRES]
        v_chg, v_note = tk.StringVar(), tk.StringVar()
        champs += [("changement", t("Eau changée (%)"), v_chg, 7), ("note", t("Remarque"), v_note, 22)]
        for col, (_c, texte, var, larg) in enumerate(champs):
            ttk.Label(saisie, text=texte, style="Carte.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 8))
            ttk.Entry(saisie, textvariable=var, width=larg).grid(row=1, column=col, sticky="w", padx=(0, 8))
        avis_cadre = tk.Frame(f, bg=CARTE)
        avis_cadre.pack(fill="x", pady=(12, 8))
        cols = [("date", t("DATE"), 90)] + [(c, t(n).upper(), 70) for c, n, *_ in eau.PARAMETRES] + \
            [("changement", t("CHANGÉE"), 70), ("note", t("REMARQUE"), 220)]
        liste = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=10, style="Liste.Treeview")
        for c, titre, l in cols:
            liste.heading(c, text=titre, anchor="w")
            liste.column(c, width=l, anchor="w")
        liste.pack(fill="both", expand=True)
        ttk.Label(f, text=t("Laissez vide ce qui n'a pas été testé. Clic droit sur une ligne pour la retirer. "
                            "Rappel si l'eau n'a pas été changée depuis {n} jours.", n=eau.RAPPEL_CHANGEMENT_J),
                  style="Discret.TLabel").pack(anchor="w", pady=(6, 0))

        def rafraichir():
            tests = eau.lire(self.dossier, bac["id"])
            liste.delete(*liste.get_children())
            for test in reversed(tests):
                liste.insert("", "end", iid=test["date"],
                             values=[fiches.date_lisible(test['date'])] +
                             [nombre(test.get(c), 2) if test.get(c) is not None else "" for c, *_ in eau.PARAMETRES] +
                             [t("{v} %", v=nombre(test["changement"])) if test.get("changement") else "",
                              test.get("note", "")])
            for w in avis_cadre.winfo_children():
                w.destroy()
            couleurs = {"alerte": ROUGE, "surveiller": AMBRE, "info": ACCENT, "ok": VERT}
            for n, texte in eau.avis(tests, bac):
                tk.Label(avis_cadre, text=f"●  {texte}", bg=CARTE, fg=couleurs.get(n, TEXTE), font=PT(10),
                         anchor="w").pack(fill="x")
            self._maj_etat_bac()

        def ajouter(_=None):
            try:
                jour = datetime.strptime(v_date.get().strip(), "%d/%m/%Y").date()
                valeurs = {c: var.get().strip() for c, _t, var, _l in champs if c in {p[0] for p in eau.PARAMETRES}}
                eau.ajouter(self.dossier, bac["id"], jour.isoformat(), valeurs, v_chg.get().strip(), v_note.get())
            except ValueError as e:
                messagebox.showerror(t("Eau"), t("Test refusé : {erreur}", erreur=e), parent=f)
                return
            for c, _t, var, _l in champs:
                if c != "date":
                    var.set("")
            rafraichir()

        def menu(ev):
            jour = liste.identify_row(ev.y)
            if jour:
                m = tk.Menu(f, tearoff=0)
                m.add_command(label=t("Retirer ce test"), command=lambda: (eau.retirer(self.dossier, bac["id"], jour), rafraichir()))
                m.tk_popup(ev.x_root, ev.y_root)

        ttk.Button(saisie, text=t("Noter"), style="Vert.TButton", command=ajouter).grid(row=1, column=len(champs), sticky="w")
        f.bind("<Return>", ajouter)
        liste.bind("<Button-3>", menu)
        rafraichir()

    # -------------------------------------------------------------- élevage
    def fenetre_elevage(self):
        f = tk.Toplevel(self.root, bg=CARTE, padx=16, pady=14)
        f.title(t("Élevage — lots d'alevins"))
        f.transient(self.root)
        f.geometry("1100x700")
        haut = tk.Frame(f, bg=CARTE)
        haut.pack(fill="x")
        ttk.Button(haut, text="➕  " + t("Nouveau lot (ponte)"), style="Vert.TButton", command=lambda: nouveau_lot()).pack(side="left")
        v_clos = tk.BooleanVar(value=False)
        ttk.Checkbutton(haut, text=t("Montrer les lots terminés"), variable=v_clos, command=lambda: rafraichir()
                        ).pack(side="left", padx=12)
        cols = (("nom", t("LOT"), 180), ("espece", t("ESPÈCE"), 130), ("bac", t("BAC"), 120), ("ponte", t("PONTE"), 90),
                ("age", t("ÂGE"), 60), ("effectif", t("ALEVINS"), 70), ("survie", t("SURVIE"), 70), ("vendus", t("VENDUS"), 70))
        lots = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=9, style="Liste.Treeview")
        for c, titre_col, l in cols:
            lots.heading(c, text=titre_col, anchor="w")
            lots.column(c, width=l, anchor="w")
        lots.pack(fill="x", pady=(10, 8))
        detail = ttk.Frame(f, style="Carte.TFrame")
        detail.pack(fill="both", expand=True)
        titre = tk.Label(detail, bg=CARTE, fg=TEXTE, font=PT(14, True), anchor="w")
        titre.pack(fill="x")
        saisie = tk.Frame(detail, bg=CARTE)
        saisie.pack(fill="x", pady=(6, 6))
        v_date = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        genres = {t(libelle): genre for genre, libelle in elevage.LIBELLES.items()}     # libellé traduit -> genre stocké
        v_genre = tk.StringVar(value=t(elevage.LIBELLES["comptage"]))
        v_nombre, v_bac, v_note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for col, (texte, widget) in enumerate((
                (t("Date"), ttk.Entry(saisie, textvariable=v_date, width=11)),
                (t("Événement"), ttk.Combobox(saisie, textvariable=v_genre, values=list(genres), state="readonly", width=12)),
                (t("Nombre"), ttk.Entry(saisie, textvariable=v_nombre, width=8)),
                (t("Vers le bac"), ttk.Combobox(saisie, textvariable=v_bac, values=[b["nom"] for b in self.cfg["bacs"]], width=16)),
                (t("Remarque"), ttk.Entry(saisie, textvariable=v_note, width=34)))):
            ttk.Label(saisie, text=texte, style="Carte.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 8))
            widget.grid(row=1, column=col, sticky="w", padx=(0, 8))
        evts = ttk.Treeview(detail, columns=("date", "genre", "nombre", "note"), show="headings", height=8, style="Liste.Treeview")
        for c, titre_col, l in (("date", t("DATE"), 100), ("genre", t("ÉVÉNEMENT"), 120), ("nombre", t("NOMBRE"), 90),
                                ("note", t("REMARQUE"), 600)):
            evts.heading(c, text=titre_col, anchor="w")
            evts.column(c, width=l, anchor="w")
        evts.pack(fill="both", expand=True)
        bas = tk.Frame(detail, bg=CARTE)
        bas.pack(fill="x", pady=(6, 0))
        courant = [None]

        def nom_bac(ident):
            b = self._bac(ident)
            return b["nom"] if b else (ident or "—")

        def rafraichir():
            lots.delete(*lots.get_children())
            for lot in self.elevage.recharger():
                if lot.get("clos") and not v_clos.get():
                    continue
                b = elevage.bilan(lot)
                lots.insert("", "end", iid=lot["id"], values=(lot["nom"] + ("  " + t("(terminé)") if lot.get("clos") else ""),
                                                              t(lot["espece"]) if lot.get("espece") else "", nom_bac(lot["bac"]),
                                                              fiches.date_lisible(lot['ponte']),
                                                              t("{n} j", n=b["age_j"]), b["effectif"],
                                                              pourcent(b["survie"]) if b["survie"] is not None else "—", b["vendus"]))
            montrer()
            self._maj_lots()

        def montrer(_=None):
            sel = lots.selection()
            courant[0] = sel[0] if sel else courant[0]
            lot = self.elevage.lot(courant[0]) if courant[0] else None
            evts.delete(*evts.get_children())
            if not lot:
                titre.configure(text=t("Choisissez un lot dans la liste"))
                return
            parents = ", ".join(self.registre.fiches[p]["nom"] for p in lot.get("parents", []) if p in self.registre.fiches)
            effectif = elevage.bilan(lot)["effectif"]
            texte = tn("{lot} — {n} alevin", "{lot} — {n} alevins", effectif, lot=lot["nom"])
            if parents:
                texte += "   ·   " + t("parents : {noms}", noms=parents)
            titre.configure(text=texte)
            for rang, e in reversed(list(enumerate(lot["evenements"]))):
                evts.insert("", "end", iid=str(rang), values=(fiches.date_lisible(e['date']),
                                                              t(elevage.LIBELLES[e["genre"]]) if e["genre"] in elevage.LIBELLES
                                                              else e["genre"],
                                                              e.get("nombre", ""), (f"→ {nom_bac(e['bac'])}  " if e.get("bac") else "") + e.get("note", "")))

        def ajouter():
            lot = self.elevage.lot(courant[0]) if courant[0] else None
            if not lot:
                return
            genre = genres[v_genre.get()]
            bac = next((b["id"] for b in self.cfg["bacs"] if b["nom"] == v_bac.get()), v_bac.get().strip() or None)
            try:
                jour = datetime.strptime(v_date.get().strip(), "%d/%m/%Y").date()
                self.elevage.ajouter(lot["id"], jour.isoformat(), genre, v_nombre.get().strip() or None, bac, v_note.get())
            except ValueError as e:
                messagebox.showerror(t("Élevage"), t("Refusé : {erreur}", erreur=e), parent=f)
                return
            v_nombre.set("")
            v_note.set("")
            rafraichir()

        def nouveau_lot():
            d = tk.Toplevel(f, bg=CARTE, padx=18, pady=14)
            d.title(t("Nouveau lot"))
            d.transient(f)
            d.grab_set()
            libelles_especes, especes = especes_affichees()
            vs = {k: tk.StringVar() for k in ("nom", "espece", "bac", "ponte", "nombre", "note")}
            vs["ponte"].set(datetime.now().strftime("%d/%m/%Y"))
            vs["espece"].set(t(vision.ESPECES_DEFAUT[0][0]))
            for rang, (cle, texte, w) in enumerate((
                    ("nom", t("Nom du lot"), ttk.Entry(d, textvariable=vs["nom"], width=30)),
                    ("espece", t("Espèce"), ttk.Combobox(d, textvariable=vs["espece"], values=libelles_especes, width=28)),
                    ("bac", t("Bac"), ttk.Combobox(d, textvariable=vs["bac"], values=[b["nom"] for b in self.cfg["bacs"]], width=28)),
                    ("ponte", t("Date de ponte"), ttk.Entry(d, textvariable=vs["ponte"], width=12)),
                    ("nombre", t("Œufs ou alevins"), ttk.Entry(d, textvariable=vs["nombre"], width=10)),
                    ("note", t("Remarque"), ttk.Entry(d, textvariable=vs["note"], width=30)))):
                ttk.Label(d, text=texte, style="Carte.TLabel").grid(row=rang, column=0, sticky="w", pady=4, padx=(0, 12))
                w.grid(row=rang, column=1, sticky="w")
            ttk.Label(d, text=t("Parents (Ctrl+clic)"), style="Carte.TLabel").grid(row=6, column=0, sticky="nw", pady=4)
            cles = sorted(self.registre.fiches, key=lambda c: self.registre.fiches[c]["nom"])
            parents = tk.Listbox(d, selectmode="multiple", height=5, bg=FOND, fg=TEXTE, selectbackground=ACCENT, relief="flat",
                                 exportselection=False)
            for c in cles:
                parents.insert("end", self.registre.fiches[c]["nom"])
            parents.grid(row=6, column=1, sticky="w")

            def creer():
                try:
                    ponte = datetime.strptime(vs["ponte"].get().strip(), "%d/%m/%Y").date()
                    bac = next((b["id"] for b in self.cfg["bacs"] if b["nom"] == vs["bac"].get()), "")
                    espece = vs["espece"].get().strip()
                    ident = self.elevage.creer(vs["nom"].get(), especes.get(espece, espece), bac, ponte.isoformat(),
                                               vs["nombre"].get() or 0, [cles[i] for i in parents.curselection()], vs["note"].get())
                except ValueError as e:
                    messagebox.showerror(t("Élevage"), t("Lot refusé : {erreur}", erreur=e), parent=d)
                    return
                d.destroy()
                courant[0] = ident
                rafraichir()
                lots.selection_set(ident)

            ttk.Button(d, text=t("Créer"), style="Vert.TButton", command=creer).grid(row=7, column=1, sticky="e", pady=(12, 0))

        def menu_lot(ev):
            ident = lots.identify_row(ev.y)
            lot = self.elevage.lot(ident)
            if not lot:
                return
            m = tk.Menu(f, tearoff=0)
            m.add_command(label=t("Rouvrir le lot") if lot.get("clos") else t("Lot terminé (vendu, grandi…)"),
                          command=lambda: (self.elevage.clore(ident, not lot.get("clos")), rafraichir()))
            m.add_command(label=t("Supprimer le lot…"), command=lambda: messagebox.askyesno(
                t("Élevage"), t("Supprimer le lot « {lot} » et tout son suivi ?", lot=lot["nom"]), parent=f) and
                (self.elevage.supprimer(ident), courant.__setitem__(0, None), rafraichir()))
            m.tk_popup(ev.x_root, ev.y_root)

        def menu_evt(ev):
            rang = evts.identify_row(ev.y)
            if rang and courant[0]:
                m = tk.Menu(f, tearoff=0)
                lot = self.elevage.lot(courant[0]) or {"evenements": []}
                attendu = lot["evenements"][int(rang)] if int(rang) < len(lot["evenements"]) else None
                m.add_command(label=t("Retirer"), command=lambda: (self.elevage.retirer(courant[0], int(rang), attendu), rafraichir()))
                m.tk_popup(ev.x_root, ev.y_root)

        ttk.Button(saisie, text=t("Noter"), style="Vert.TButton", command=ajouter).grid(row=1, column=5, sticky="w")
        ttk.Label(bas, text=t("Comptage et éclosion fixent l'effectif ; pertes, vente, cession et tri le réduisent ; "
                              "transfert change le lot de bac. Clic droit : terminer, supprimer."), style="Discret.TLabel").pack(anchor="w")
        lots.bind("<<TreeviewSelect>>", montrer)
        lots.bind("<Button-3>", menu_lot)
        evts.bind("<Button-3>", menu_evt)
        rafraichir()

    # -------------------------------------------------------------- téléphone et alertes
    def _maj_alertes(self):
        if self.cfg["ntfy_actif"] and self.cfg["ntfy_sujet"]:
            self.alertes = alertes.Alertes(self.dossier, self.cfg["ntfy_serveur"], self.cfg["ntfy_sujet"],
                                           sur_erreur=lambda texte: self.messages.put(("message", texte)))
        else:
            self.alertes = None

    def _etat_bacs(self):
        sortie = []
        for b in self.cfg["bacs"]:
            bil = self.bilans.get(b["id"], {}) if b["id"] in self.surveillances else {}
            sortie.append({"id": b["id"], "nom": b["nom"], "type": b.get("type", "aquarium"),
                           "etat": bil.get("etat", "inconnu") if b["id"] in self.surveillances else "arrete",
                           "raisons": bil.get("raisons", []), "nb": bil.get("nb"), "attendus": bil.get("attendus")})
        return sortie

    def _web(self, actif, silencieux=False):
        if self.serveur_web:
            self.serveur_web.arreter()
            self.serveur_web = None
        if actif and web is None:
            if not silencieux:
                messagebox.showerror(t("Téléphone"), t("La page du téléphone n'est pas installée (web.py manquant)."))
            actif = False
        if actif:
            if not self.cfg["web_code"]:
                self.cfg["web_code"] = web.nouveau_code()
            if not self.cfg.get("web_secret"):
                self.cfg["web_secret"] = secrets.token_hex(32)
            prefere = self.cfg.get("web_port") or web.PORT
            for port in [prefere] + [p for p in range(web.PORT, web.PORT + 10) if p != prefere]:
                try:
                    self.serveur_web = web.Serveur(self.registre, lambda: self.dossier, self._etat_bacs,
                                                   lambda i: self.images.get(i) if i in self.surveillances else None,
                                                   self.cfg["web_code"], port=port, secret=self.cfg["web_secret"],
                                                   apres_modif=lambda: self.messages.put(("poissons", None))).demarrer()
                    self.cfg["web_port"] = port
                    break
                except OSError:
                    continue
            if self.serveur_web is None:
                if not silencieux:
                    messagebox.showerror(t("Téléphone"), t("Accès impossible : aucun port libre."))
                actif = False
        if actif or not silencieux:         # échec au démarrage : on réessaiera la prochaine fois
            self.cfg["web_actif"] = actif
            self._ecrire_config()
        return actif

    def telephone(self):
        f = tk.Toplevel(self.root, bg=CARTE, padx=22, pady=18)
        f.title(t("Frétille sur le téléphone"))
        f.transient(self.root)
        f.resizable(False, False)
        gauche = tk.Frame(f, bg=CARTE)
        gauche.pack(side="left", anchor="n")
        tk.Frame(f, bg=BORD, width=1).pack(side="left", fill="y", padx=22)
        droite = tk.Frame(f, bg=CARTE)
        droite.pack(side="left", anchor="n")

        def qr_png(texte, echelle):
            try:
                import segno
            except ImportError:
                return None
            tampon = io.BytesIO()
            segno.make(texte, error="m").save(tampon, kind="png", scale=echelle, border=2, dark="#0b1418", light="#ffffff")
            tampon.seek(0)
            return ImageTk.PhotoImage(Image.open(tampon))

        v_web = tk.BooleanVar(value=self.serveur_web is not None)
        ttk.Checkbutton(gauche, text=t("Accès depuis le téléphone (Wi-Fi de la maison)"), variable=v_web,
                        command=lambda: (self._web(v_web.get()), montrer())).pack(anchor="w")
        qr = tk.Label(gauche, bg=CARTE)
        qr.pack(pady=(14, 8))
        lbl_url = tk.Label(gauche, bg=CARTE, fg=TEXTE, font=PT(13, True))
        lbl_url.pack()
        lbl_code = tk.Label(gauche, bg=CARTE, fg=AMBRE, font=PT(13, True))
        lbl_code.pack(pady=(4, 0))
        ttk.Label(gauche, style="Discret.TLabel", justify="left", wraplength=420, text=t(
            "Scannez le QR code avec l'appareil photo du téléphone, branché sur le même Wi-Fi que ce PC.\n\n"
            "Au premier lancement, Windows demande d'autoriser Python : cochez « Réseaux privés ».\n"
            "La page n'est pas visible depuis Internet.")).pack(anchor="w", pady=(12, 0))
        bas = tk.Frame(gauche, bg=CARTE)
        bas.pack(fill="x", pady=(14, 0))

        def nouveau():
            if web and messagebox.askyesno(t("Téléphone"), t("Changer le code ? Les téléphones connectés devront rescanner."),
                                           parent=f):
                self.cfg["web_code"] = web.nouveau_code()
                self._web(True)
                montrer()

        btn_code = ttk.Button(bas, text=t("Nouveau code"), command=nouveau)
        btn_code.pack(side="left")
        ttk.Button(bas, text=t("Fermer"), command=f.destroy).pack(side="right")

        def montrer():
            s = self.serveur_web
            v_web.set(s is not None)
            if s is None:
                qr.configure(image="", text=t("Accès désactivé"), fg=DISCRET, font=PT(11), width=30, height=10)
                lbl_url.configure(text="")
                lbl_code.configure(text="")
                btn_code.configure(state="disabled")
                return
            btn_code.configure(state="normal")
            qr.image = qr_png(s.url(), 8)
            qr.configure(image=qr.image or "", text="" if qr.image else t("(installez segno)"), width=0, height=0)
            lbl_url.configure(text=s.url(avec_code=False))
            lbl_code.configure(text=t("Code : {code}", code=s.code))

        v_ntfy = tk.BooleanVar(value=bool(self.alertes))

        def basculer_ntfy():
            if v_ntfy.get() and not self.cfg["ntfy_sujet"]:
                self.cfg["ntfy_sujet"] = alertes.nouveau_sujet()
            self.cfg["ntfy_actif"] = v_ntfy.get()
            self._ecrire_config()
            self._maj_alertes()
            montrer_ntfy()

        ttk.Checkbutton(droite, text=t("Alertes sur le téléphone (appli ntfy)"), variable=v_ntfy, command=basculer_ntfy).pack(anchor="w")
        qr_ntfy = tk.Label(droite, bg=CARTE)
        qr_ntfy.pack(pady=(14, 8))
        lbl_sujet = tk.Label(droite, bg=CARTE, fg=AMBRE, font=PT(11, True), wraplength=380)
        lbl_sujet.pack()
        ttk.Label(droite, style="Discret.TLabel", justify="left", wraplength=380, text=t(
            "1. Installez l'appli gratuite « ntfy » (Play Store ou App Store).\n"
            "2. Touchez « + », puis scannez le QR code ou collez le nom du sujet.\n\n"
            "Vous serez prévenu, même loin de la maison : prédateur au bassin (avec photo), poisson immobile "
            "en surface, poissons en manque d'oxygène, poissons non vus, apathiques ou agités, eau dangereuse, "
            "image figée.\nLes alertes passent par le service public ntfy.sh ; le nom du sujet, tiré au hasard, "
            "est la seule clé : ne le partagez pas.")).pack(anchor="w", pady=(10, 0))
        bas_ntfy = tk.Frame(droite, bg=CARTE)
        bas_ntfy.pack(fill="x", pady=(12, 0))

        def tester():
            a = self.alertes
            if not a:
                return

            def travail():
                try:
                    alertes.envoyer(a.serveur, a.sujet, "Frétille", t("Test : les alertes arrivent bien sur ce téléphone."))
                    self.messages.put(("message", t("Alerte de test envoyée")))
                except OSError as e:
                    self.messages.put(("message", t("Alerte de test impossible : {erreur}", erreur=e)))
            threading.Thread(target=travail, daemon=True).start()

        btn_test = ttk.Button(bas_ntfy, text=t("Envoyer un test"), command=tester)
        btn_test.pack(side="left")

        def montrer_ntfy():
            if not self.alertes:
                qr_ntfy.configure(image="", text=t("Alertes désactivées"), fg=DISCRET, font=PT(11), width=30, height=10)
                lbl_sujet.configure(text="")
                btn_test.configure(state="disabled")
                return
            btn_test.configure(state="normal")
            qr_ntfy.image = qr_png(alertes.adresse_abonnement(self.cfg["ntfy_serveur"], self.cfg["ntfy_sujet"]), 6)
            qr_ntfy.configure(image=qr_ntfy.image or "", text="", width=0, height=0)
            lbl_sujet.configure(text=t("Sujet : {sujet}", sujet=self.cfg["ntfy_sujet"]))

        montrer()
        montrer_ntfy()

    # -------------------------------------------------------------- plein écran (direct)
    def plein_ecran(self):
        """Vidéo du bac affiché, en plein écran, habillée pour être diffusée (YouTube…) : noms des
        poissons reconnus, « EN DIRECT », heure, nombre de poissons. Rien de privé ni de technique.
        Échap, F11 ou double-clic pour sortir ; P enregistre une photo 1920×1080 (dossier « partage »)."""
        if self.diffusion is not None:
            self._quitter_diffusion()
            return
        f = tk.Toplevel(self.root, bg="black", cursor="none")
        f.title(t("Frétille — en direct"))
        f.protocol("WM_DELETE_WINDOW", self._quitter_diffusion)
        f.attributes("-fullscreen", True)
        f.attributes("-topmost", True)
        ecran = tk.Label(f, bg="black", bd=0)
        ecran.pack(fill="both", expand=True)
        self.diffusion, self.ecran_diffusion, self.image_diffusion, self.derniere_diffusion = f, ecran, None, None
        self.tour_diffusion = getattr(self, "tour_diffusion", 0) + 1
        self.f_diff = {"titre": police(34, True), "nom": police(28, True), "petit": police(20), "heure": police(40, True),
                       "attente": police(36)}
        for touche in ("<Escape>", "<F11>", "<Double-1>"):
            f.bind(touche, lambda _e: self._quitter_diffusion())
        f.bind("<KeyPress-p>", lambda _e: self._photo_diffusion())
        f.bind("<KeyPress-P>", lambda _e: self._photo_diffusion())
        f.focus_force()
        f.after(200, self._diffusion_au_repos, self.tour_diffusion)

    def _quitter_diffusion(self):
        if self.diffusion is not None:
            self.diffusion.destroy()
        self.diffusion = self.image_diffusion = self.derniere_diffusion = None
        if self.bac_courant not in self.surveillances:
            self._afficher_vide()

    def _diffusion_au_repos(self, tour):
        if self.diffusion is None or not self.diffusion.winfo_exists() or tour != self.tour_diffusion:
            return
        if self.bac_courant not in self.surveillances:
            lw, lh = max(self.diffusion.winfo_width(), 640), max(self.diffusion.winfo_height(), 360)
            img = Image.new("RGB", (lw, lh), (5, 10, 12))
            d = ImageDraw.Draw(img, "RGBA")
            try:
                logo = Image.open(ICI / "fretille.png").convert("RGBA").resize((200, 200), Image.LANCZOS)
                img.paste(logo, ((lw - 200) // 2, lh // 2 - 230), logo)
            except OSError:
                pass
            d.text((lw / 2, lh / 2 + 30), t("Le direct revient dans un instant"), font=self.f_diff["attente"],
                   fill=(230, 241, 243), anchor="mt")
            self._habillage(d, (lw, lh), None, en_direct=False)
            self._montrer_diffusion(img)
        self.diffusion.after(1000, self._diffusion_au_repos, tour)

    def _habillage(self, d, taille, etat, en_direct=True):
        lw, lh = taille
        F = self.f_diff
        d.rounded_rectangle([24, 24, 60 + d.textlength("Frétille", font=F["titre"]), 84], radius=16, fill=(11, 20, 24, 190))
        d.text((42, 54), "Frétille", font=F["titre"], fill=(230, 241, 243), anchor="lm")
        if en_direct:
            x = 76 + d.textlength("Frétille", font=F["titre"])
            en_direct_ = t("EN DIRECT")
            larg = d.textlength(en_direct_, font=F["petit"]) + 52
            d.rounded_rectangle([x, 34, x + larg, 74], radius=20, fill=(248, 81, 73, 235))
            d.ellipse([x + 16, 47, x + 30, 61], fill="white")
            d.text((x + 38, 54), en_direct_, font=F["petit"], fill="white", anchor="lm")
        maintenant = datetime.now()
        heure = maintenant.strftime("%H:%M")
        jour = t("{jour} {mois}", jour=maintenant.day, mois=t(fiches.MOIS[maintenant.month - 1]))
        lh_h = d.textlength(heure, font=F["heure"])
        d.rounded_rectangle([lw - lh_h - 70, 24, lw - 24, 120], radius=16, fill=(11, 20, 24, 190))
        d.text((lw - 47, 30), heure, font=F["heure"], fill=(230, 241, 243), anchor="ra")
        d.text((lw - 47, 84), jour, font=F["petit"], fill=(138, 165, 173), anchor="ra")
        bac = self._bac(self.bac_courant)
        if bac and etat is not None:
            texte = f"{bac['nom']}   ·   " + tn("{n} poisson à l'image", "{n} poissons à l'image", etat.get("nb", 0))
            tw = d.textlength(texte, font=F["petit"])
            d.rounded_rectangle([24, lh - 76, 24 + tw + 40, lh - 24], radius=16, fill=(11, 20, 24, 200))
            d.text((44, lh - 50), texte, font=F["petit"], fill=(230, 241, 243), anchor="lm")

    def _afficher_diffusion(self, image, etat):
        f = self.diffusion
        lw, lh = max(f.winfo_width(), 640), max(f.winfo_height(), 360)
        h, w = image.shape[:2]
        e = min(lw / w, lh / h)
        nw, nh = int(w * e), int(h * e)
        video = Image.fromarray(cv2.cvtColor(cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB))
        img = video if (nw, nh) == (lw, lh) else Image.new("RGB", (lw, lh))
        if img is not video:
            img.paste(video, ((lw - nw) // 2, (lh - nh) // 2))
        ox, oy = (lw - nw) // 2, (lh - nh) // 2
        d = ImageDraw.Draw(img, "RGBA")
        F = self.f_diff
        for _n, boite, texte, sure, cle in etat.get("pistes", []):
            fiche = self.registre.fiches.get(cle) if cle and texte and not texte.endswith("?") else None
            if not fiche:
                continue                    # seuls les poissons reconnus sont présentés au public
            x, y, bw, bh = (int(v * e) for v in boite)
            x, y = x + ox, y + oy
            age = fiches.age_lisible(fiche.get("naissance", ""))
            nom = fiche["nom"]
            tw = max(d.textlength(nom, font=F["nom"]), d.textlength(age, font=F["petit"]) if age else 0)
            ht = 70 if age else 46
            yt = max(0, y - ht - 6)
            d.rounded_rectangle([x, y, x + bw, y + bh], radius=12, outline=(240, 180, 76, 255), width=3)
            d.rounded_rectangle([x, yt, x + tw + 28, yt + ht], radius=12, fill=(11, 20, 24, 215))
            d.text((x + 14, yt + 4), nom, font=F["nom"], fill=(230, 241, 243))
            if age:
                d.text((x + 14, yt + 42), age, font=F["petit"], fill=(240, 180, 76))
        if etat.get("figee_s"):
            texte = t("Le direct revient dans un instant")
            tw = d.textlength(texte, font=F["attente"])
            d.rounded_rectangle([(lw - tw) / 2 - 30, lh / 2 - 40, (lw + tw) / 2 + 30, lh / 2 + 40], radius=20, fill=(11, 20, 24, 220))
            d.text((lw / 2, lh / 2), texte, font=F["attente"], fill=(230, 241, 243), anchor="mm")
        self._habillage(d, (lw, lh), etat)
        self._montrer_diffusion(img)

    def _montrer_diffusion(self, img):
        self.derniere_diffusion = img
        i = self.image_diffusion
        if i is not None and (i.width(), i.height()) == img.size:
            i.paste(img)
        else:
            self.image_diffusion = ImageTk.PhotoImage(img)
            self.ecran_diffusion.configure(image=self.image_diffusion)

    def _photo_diffusion(self):
        if self.derniere_diffusion is None:
            return
        d = self.dossier / "partage"
        d.mkdir(parents=True, exist_ok=True)
        img = self.derniere_diffusion.copy()
        img.thumbnail((1920, 1080), Image.LANCZOS)
        fond = Image.new("RGB", (1920, 1080))
        fond.paste(img, ((1920 - img.width) // 2, (1080 - img.height) // 2))
        chemin = d / f"fretille_{datetime.now():%Y-%m-%d_%H-%M-%S}.jpg"
        fond.save(chemin, quality=92)
        self.lbl_etat.configure(text=t("Photo à partager : {fichier} (dossier « partage »)", fichier=chemin.name))

    # -------------------------------------------------------------- réglages généraux
    def reglages(self):
        f = tk.Toplevel(self.root, bg=CARTE, padx=20, pady=16)
        f.title(t("Réglages"))
        f.transient(self.root)
        f.grab_set()
        langues = [("", t("Langue de Windows"))] + list(i18n.LANGUES.items())      # (code, nom affiché)
        langue_avant = self.cfg.get("langue") or ""
        v_langue = tk.StringVar(value=next((nom for code, nom in langues if code == langue_avant), langues[0][1]))
        v_dossier = tk.StringVar(value=self.cfg["dossier"])
        v_analyse = tk.StringVar(value=str(self.cfg["analyse_toutes"] or ""))
        v_reco = tk.BooleanVar(value=self.cfg["reconnaissance"])
        v_intrus = tk.BooleanVar(value=self.cfg["alerte_intrus"])
        v_auto = tk.BooleanVar(value=self.cfg["demarrage_auto"])
        v_windows = tk.BooleanVar(value=demarrage.est_active())
        v_sauvegarde = tk.StringVar(value=self.cfg["sauvegarde_dossier"])

        def ligne(rang, texte, var, largeur=44, choisir=False):
            ttk.Label(f, text=texte, style="Carte.TLabel").grid(row=rang, column=0, sticky="w", pady=6, padx=(0, 14))
            ttk.Entry(f, textvariable=var, width=largeur).grid(row=rang, column=1, sticky="w")
            if choisir:
                ttk.Button(f, text="…", width=3, command=lambda: var.set(filedialog.askdirectory(parent=f) or var.get())
                           ).grid(row=rang, column=2, padx=(6, 0))

        ligne(0, t("Dossier des données (mesures, photos, clips)"), v_dossier, choisir=True)
        ligne(1, t("Analyse toutes les … secondes (vide = auto)"), v_analyse, 8)
        ligne(2, t("Sauvegarde quotidienne dans (clé USB, OneDrive…)"), v_sauvegarde, choisir=True)
        for rang, (texte, var) in enumerate(((t("Reconnaître les poissons un par un"), v_reco),
                                             (t("Bassins : surveiller les prédateurs (héron, chat…)"), v_intrus),
                                             (t("Démarrer la surveillance de tous les bacs à l'ouverture"), v_auto),
                                             (t("Ouvrir Frétille au démarrage de Windows"), v_windows)), start=3):
            ttk.Checkbutton(f, text=texte, variable=var).grid(row=rang, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(f, text=t("Langue"), style="Carte.TLabel").grid(row=7, column=0, sticky="w", pady=6, padx=(0, 14))
        ttk.Combobox(f, textvariable=v_langue, values=[nom for _code, nom in langues], state="readonly", width=28
                     ).grid(row=7, column=1, sticky="w")

        def enregistrer():
            try:
                analyse = float(v_analyse.get().replace(",", ".")) if v_analyse.get().strip() else 0
                if analyse and not 1 <= analyse <= 120:
                    raise ValueError
            except ValueError:
                messagebox.showerror(t("Réglages"), t("Analyse : entre 1 et 120 secondes, ou vide."), parent=f)
                return
            nouveau_dossier = v_dossier.get().strip() != self.cfg["dossier"]
            if nouveau_dossier:
                try:                            # registre du nouveau dossier lisible ? sinon on ne bouge rien
                    nouveau_registre = Registre(Path(v_dossier.get().strip()), self.registre.descripteur)
                except RegistreIllisible as err:
                    messagebox.showerror(t("Réglages"), str(err), parent=f)
                    return
            if nouveau_dossier and self.surveillances:
                messagebox.showerror(t("Réglages"), t("Arrêtez d'abord la surveillance des bacs pour changer de dossier."), parent=f)
                return
            try:
                if v_windows.get() and not demarrage.est_active():
                    demarrage.activer()
                elif not v_windows.get() and demarrage.est_active():
                    demarrage.desactiver()
            except OSError as e:
                messagebox.showerror(t("Réglages"), t("Démarrage de Windows : {erreur}", erreur=e), parent=f)
            langue = next((code for code, nom in langues if nom == v_langue.get()), langue_avant)
            self.cfg.update(dossier=v_dossier.get().strip(), analyse_toutes=analyse, reconnaissance=v_reco.get(),
                            alerte_intrus=v_intrus.get(), demarrage_auto=v_auto.get(), sauvegarde_dossier=v_sauvegarde.get().strip(),
                            langue=langue)
            self._ecrire_config()
            f.destroy()
            if langue != langue_avant:
                messagebox.showinfo(t("Réglages"), t("La nouvelle langue s'appliquera au prochain démarrage de Frétille."))
            if nouveau_dossier:
                for cle in list(self.fenetres_fiche):
                    self._fermer_fiche(cle)
                self.registre = nouveau_registre
                self.elevage = elevage.Elevage(self.dossier)
                threading.Thread(target=self._precharger, daemon=True).start()
                if self.serveur_web:
                    self._web(True)
                self._maj_alertes()
                self._maj_evenements()
                self._maj_poissons()
                self._maj_lots()

        ttk.Button(f, text=t("Enregistrer"), style="Vert.TButton", command=enregistrer).grid(row=8, column=1, sticky="e", pady=(14, 0))

    # -------------------------------------------------------------- sauvegarde et fermeture
    def _sauvegarde_auto(self):
        if self.cfg["sauvegarde_dossier"]:
            def travail():
                try:
                    r = sauvegarde.sauvegarder(self.dossier, self.cfg["sauvegarde_dossier"])
                    if r:
                        self.messages.put(("message", tn("Sauvegarde faite : {nom} ({n} fichier)",
                                                         "Sauvegarde faite : {nom} ({n} fichiers)", r[1], nom=r[0].name)))
                except OSError as e:
                    self.messages.put(("message", t("Sauvegarde impossible : {erreur}", erreur=e)))
            threading.Thread(target=travail, daemon=True).start()
        if self._tk_vivant():
            self.root.after(3600 * 1000, self._sauvegarde_auto)

    def quitter(self):
        for cle, f in list(self.fenetres_fiche.items()):
            if f.winfo_exists() and hasattr(f, "fermer"):
                f.fermer()
                if f.winfo_exists():
                    return
        self._ecrire_config()
        if self.serveur_web:
            self.serveur_web.arreter()
            self.serveur_web = None
        if not self.surveillances:
            self.root.destroy()
            return
        # chaque bac termine sa minute de mesures et son clip ; filet d'une minute
        self.fermeture = True
        if self.diffusion is not None:
            self._quitter_diffusion()
        self.root.withdraw()
        for s in list(self.surveillances.values()):
            s.arreter()
        self.root.after(60000, self.root.destroy)


def main():
    root = tk.Tk()
    try:
        App(root)
        root.mainloop()
    except Exception as e:
        journaliser_erreur(e)
        raise


if __name__ == "__main__":
    main()
