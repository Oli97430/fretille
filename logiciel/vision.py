"""
Frétille — vision : trouver les poissons (et les prédateurs) dans l'image, dire leur espèce,
décrire leur robe pour reconnaître chaque poisson.

  - détection     : Grounding DINO tiny (zero-shot, Apache 2.0), invité à chercher « poisson »,
                    et au bassin « oiseau, chat, personne » : les prédateurs sortent de la même passe
  - espèce        : BioCLIP (MIT) en zero-shot sur les noms scientifiques
  - prédateur     : BioCLIP en zero-shot (héron, martin-pêcheur, chat, rapace…)
  - individu      : MegaDescriptor (ré-identification animale, CC BY-NC 4.0) + SIFT (motif de la robe :
                    fiable pour les poissons à motifs uniques, koï, discus, bettas, poissons rouges tachés)
"""

import threading

import cv2
import numpy as np

MODELE_DETECTION = "IDEA-Research/grounding-dino-tiny"
MODELE_ESPECES = "hf-hub:imageomics/bioclip"
MODELE_REID = "hf-hub:BVRA/MegaDescriptor-L-384"
TAILLE_POISSON = 384

# (nom affiché, nom scientifique, nom anglais)
ESPECES_DEFAUT = [
    ("Koï", "Cyprinus rubrofuscus", "koi carp"),
    ("Poisson rouge", "Carassius auratus", "goldfish"),
    ("Carpe commune", "Cyprinus carpio", "common carp"),
    ("Ide mélanote (orfe)", "Leuciscus idus", "golden orfe"),
    ("Esturgeon sibérien", "Acipenser baerii", "Siberian sturgeon"),
    ("Tilapia", "Oreochromis niloticus", "Nile tilapia"),
    ("Guppy", "Poecilia reticulata", "guppy"),
    ("Molly", "Poecilia sphenops", "molly"),
    ("Platy", "Xiphophorus maculatus", "platy"),
    ("Xipho", "Xiphophorus hellerii", "green swordtail"),
    ("Néon", "Paracheirodon innesi", "neon tetra"),
    ("Cardinalis", "Paracheirodon axelrodi", "cardinal tetra"),
    ("Discus", "Symphysodon aequifasciatus", "discus"),
    ("Scalaire", "Pterophyllum scalare", "freshwater angelfish"),
    ("Combattant (betta)", "Betta splendens", "Siamese fighting fish"),
    ("Gourami nain", "Trichogaster lalius", "dwarf gourami"),
    ("Corydoras", "Corydoras paleatus", "peppered corydoras"),
    ("Ancistrus", "Ancistrus cirrhosus", "bristlenose pleco"),
    ("Cichlidé du Malawi", "Maylandia zebra", "zebra mbuna"),
    ("Ramirezi", "Mikrogeophagus ramirezi", "ram cichlid"),
    ("Barbus de Sumatra", "Puntigrus tetrazona", "tiger barb"),
    ("Danio zébré", "Danio rerio", "zebrafish"),
    ("Poisson-clown", "Amphiprion ocellaris", "clownfish"),
]
NON_POISSONS = ["a photo of aquarium plants.", "a photo of gravel and stones.", "a photo of an aquarium filter.",
                "a photo of water bubbles.", "a photo of a reflection on glass.", "a photo of a pond with leaves.",
                "a photo of a snail.", "a photo of a shrimp.", "a dark blurry photo."]
# prédateurs du bassin : (étiquette, texte BioCLIP, est un danger)
PREDATEURS = [
    ("Héron", "a photo of a heron standing in water.", True),
    ("Aigrette", "a photo of an egret.", True),
    ("Martin-pêcheur", "a photo of a kingfisher.", True),
    ("Rapace", "a photo of a bird of prey.", True),
    ("Chat", "a photo of a cat.", True),
    ("Rat", "a photo of a rat.", True),
    ("Loutre", "a photo of an otter.", True),
    ("Raton laveur", "a photo of a raccoon.", True),
    ("Petit oiseau", "a photo of a small songbird.", False),
    ("Canard", "a photo of a duck.", False),
    ("Personne", "a photo of a person.", False),
    ("Chien", "a photo of a dog.", False),
    ("Feuilles", "a photo of leaves and plants.", False),
]
CHERCHE_AQUARIUM = "fish."
CHERCHE_BASSIN = "fish. bird. heron. cat. person."
# Les noms français ci-dessus sont des clés (stockées sur disque) : on les traduit à l'affichage, par t(nom).


def _cles_a_traduire():
    """Jamais appelée : signale à tests/cles_i18n.py les noms d'espèces et de prédateurs, traduits
    à l'affichage par t(nom). Rien n'est traduit à l'import (la langue n'est pas encore choisie)."""
    from i18n import t
    return (t("Koï"), t("Poisson rouge"), t("Carpe commune"), t("Ide mélanote (orfe)"), t("Esturgeon sibérien"),
            t("Tilapia"), t("Guppy"), t("Molly"), t("Platy"), t("Xipho"), t("Néon"), t("Cardinalis"), t("Discus"),
            t("Scalaire"), t("Combattant (betta)"), t("Gourami nain"), t("Corydoras"), t("Ancistrus"),
            t("Cichlidé du Malawi"), t("Ramirezi"), t("Barbus de Sumatra"), t("Danio zébré"), t("Poisson-clown"),
            t("Héron"), t("Aigrette"), t("Martin-pêcheur"), t("Rapace"), t("Chat"), t("Rat"), t("Loutre"),
            t("Raton laveur"), t("Petit oiseau"), t("Canard"), t("Personne"), t("Chien"), t("Feuilles"))


def appareil():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    l = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - l * h
    return l * h / union if union else 0.0


def contenue(petite, grande):
    """Part de la petite boîte qui se trouve dans la grande."""
    ax, ay, aw, ah = petite
    bx, by, bw, bh = grande
    l = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0, min(ay + ah, by + bh) - max(ay, by))
    return l * h / max(aw * ah, 1)


def trier_boites(boites):
    """Une boîte par poisson : le détecteur sort parfois tout un banc dans une seule boîte (elle en
    contient plusieurs autres), ou une nageoire seule (contenue dans un poisson). Ni l'une ni l'autre."""
    def dedans(g):
        return [p for p in boites if p is not g and p[1][2] * p[1][3] < g[1][2] * g[1][3] and contenue(p[1], g[1]) > 0.7]

    groupes = [g for g in boites if len(dedans(g)) >= 2]
    gardees = [b for b in boites if b not in groupes]
    return [p for p in gardees if not any(p in dedans(g) for g in gardees)]


class Detecteur:
    """Boîtes des poissons, et au bassin des intrus (oiseau, chat, personne)."""

    def __init__(self, seuil=0.28, largeur=960, cote_min=12):
        self.seuil, self.largeur, self.cote_min = seuil, largeur, cote_min
        self.modele = None
        self._verrou = threading.Lock()

    def charger(self):
        with self._verrou:
            if self.modele is None:
                from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
                self.processeur = AutoProcessor.from_pretrained(MODELE_DETECTION)
                self.appareil = appareil()
                self.modele = AutoModelForZeroShotObjectDetection.from_pretrained(MODELE_DETECTION).to(self.appareil).eval()
                # les poissons d'aquarium sont petits : on garde plus de résolution que pour les tortues
                self.cote = 800 if self.appareil == "cuda" else 560

    def detecter(self, image_bgr, texte=CHERCHE_AQUARIUM):
        """(poissons, intrus) : listes de (score, (x, y, l, h)[, étiquette]) en pixels de l'image d'origine.
        Plusieurs bacs peuvent partager un détecteur : un appel à la fois."""
        import torch
        from PIL import Image
        self.charger()
        e = min(1.0, self.largeur / max(image_bgr.shape[:2]))
        petite = cv2.resize(image_bgr, None, fx=e, fy=e, interpolation=cv2.INTER_AREA) if e < 1 else image_bgr
        image = Image.fromarray(cv2.cvtColor(petite, cv2.COLOR_BGR2RGB))
        with self._verrou:
            entrees = self.processeur(images=image, text=texte, return_tensors="pt",
                                      size={"shortest_edge": self.cote, "longest_edge": int(self.cote * 1.34)}).to(self.appareil)
            with torch.no_grad():
                sorties = self.modele(**entrees)
            r = self.processeur.post_process_grounded_object_detection(
                sorties, entrees["input_ids"], threshold=self.seuil, text_threshold=self.seuil,
                target_sizes=[image.size[::-1]])[0]
        etiquettes = r.get("text_labels") or r.get("labels") or [""] * len(r["scores"])
        poissons, intrus = [], []
        for s, b, t in sorted(zip(r["scores"].tolist(), r["boxes"].tolist(), etiquettes), key=lambda x: -x[0]):
            x0, y0, x1, y1 = [v / e for v in b]
            boite = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
            if min(boite[2:]) < self.cote_min:
                continue
            t = str(t).strip().lower()
            if "fish" in t or not t:
                if all(iou(boite, autre[1]) < 0.5 for autre in poissons):
                    poissons.append((float(s), boite))
            elif all(iou(boite, autre[1]) < 0.5 for autre in intrus):
                intrus.append((float(s), boite, t))
        return sorted(trier_boites(poissons), key=lambda d: d[1][2] * d[1][3], reverse=True), intrus


def recadrer(image, boite, marge=0.08):
    """Recadrage carré autour d'une boîte, sans sortir de l'image."""
    h, w = image.shape[:2]
    x, y, bw, bh = boite
    cote = min(int(max(bw, bh) * (1 + 2 * marge)), w, h)
    x0 = max(0, min(w - cote, x + bw // 2 - cote // 2))
    y0 = max(0, min(h - cote, y + bh // 2 - cote // 2))
    return image[y0:y0 + cote, x0:x0 + cote]


def isoler(image, boite):
    """Poisson seul sur fond gris (TAILLE_POISSON²) : plantes, sable et reflets changent, ils ne
    doivent pas compter dans l'identité. Si la découpe GrabCut échoue, le recadrage simple est gardé."""
    t = 192
    carre = recadrer(image, boite, 0.06)
    if carre.size == 0:
        return None
    cote = carre.shape[0]
    petite = cv2.resize(carre, (t, t), interpolation=cv2.INTER_AREA)
    x0 = max(0, min(image.shape[1] - cote, boite[0] + boite[2] // 2 - cote // 2))
    y0 = max(0, min(image.shape[0] - cote, boite[1] + boite[3] // 2 - cote // 2))
    f = t / cote
    bx0, by0 = max(0, int((boite[0] - x0) * f)), max(0, int((boite[1] - y0) * f))
    bx1, by1 = min(t, int((boite[0] + boite[2] - x0) * f)), min(t, int((boite[1] + boite[3] - y0) * f))
    centre = ((bx0 + bx1) // 2, (by0 + by1) // 2)
    masque = np.full((t, t), cv2.GC_BGD, np.uint8)
    masque[by0:by1, bx0:bx1] = cv2.GC_PR_BGD
    cv2.ellipse(masque, centre, (max(1, (bx1 - bx0) * 2 // 5), max(1, (by1 - by0) * 2 // 5)), 0, 0, 360, cv2.GC_PR_FGD, -1)
    cv2.ellipse(masque, centre, (max(1, (bx1 - bx0) // 5), max(1, (by1 - by0) // 5)), 0, 0, 360, cv2.GC_FGD, -1)
    simple = cv2.resize(carre, (TAILLE_POISSON, TAILLE_POISSON), interpolation=cv2.INTER_AREA)
    try:
        cv2.grabCut(petite, masque, None, np.zeros((1, 65)), np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return simple
    objet = np.isin(masque, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    objet = cv2.morphologyEx(objet, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    contours, _h = cv2.findContours(objet, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours or cv2.contourArea(max(contours, key=cv2.contourArea)) < 0.25 * (bx1 - bx0) * (by1 - by0):
        return simple
    plein = np.zeros((t, t), np.uint8)
    cv2.drawContours(plein, [max(contours, key=cv2.contourArea)], -1, 255, -1)
    plein = cv2.GaussianBlur(cv2.resize(plein, (TAILLE_POISSON, TAILLE_POISSON)), (9, 9), 0)[..., None] / 255.0
    return (simple * plein + 128 * (1 - plein)).astype(np.uint8)


class Classeur:
    """BioCLIP zero-shot : poisson ou pas, espèce parmi celles de l'utilisateur, et nature d'un intrus."""

    def __init__(self, especes=None):
        self.especes = especes or ESPECES_DEFAUT[:2]
        self.modele = None
        self._verrou = threading.RLock()

    def charger(self):
        with self._verrou:
            if self.modele is not None:
                return
            import open_clip
            self.modele, _e, self.pretraitement = open_clip.create_model_and_transforms(MODELE_ESPECES)
            self.jetons = open_clip.get_tokenizer(MODELE_ESPECES)
            self.appareil = appareil()
            self.modele.to(self.appareil).eval()
            self.t_poisson = self._textes(["a photo of a fish.", "a photo of Actinopterygii."] + NON_POISSONS)
            self.t_especes = self._textes([f"a photo of {e[1]}, {e[2]}." for e in self.especes])
            self.t_intrus = self._textes([p[1] for p in PREDATEURS])

    def _textes(self, textes):
        import torch
        with torch.no_grad():
            v = self.modele.encode_text(self.jetons(textes).to(self.appareil)).float()
        return (v / v.norm(dim=-1, keepdim=True)).cpu().numpy()

    def vecteur(self, image_bgr):
        import torch
        from PIL import Image
        self.charger()
        with self._verrou, torch.no_grad():
            image = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
            f = self.modele.encode_image(self.pretraitement(image)[None].to(self.appareil)).float()
            return (f / f.norm(dim=-1, keepdim=True)).cpu().numpy()[0]

    @staticmethod
    def _proba(textes, f):
        logits = 100.0 * textes @ f
        p = np.exp(logits - logits.max())
        return p / p.sum()

    def classer(self, image_bgr):
        """(prob. que ce soit un poisson, [(espèce, prob.)...] triées)."""
        f = self.vecteur(image_bgr)
        p = self._proba(self.t_poisson, f)
        q = self._proba(self.t_especes, f)
        return float(p[:2].sum()), [(self.especes[i][0], float(q[i])) for i in np.argsort(-q)]

    def intrus(self, image_bgr):
        """(étiquette, prob., est un danger) du meilleur candidat parmi les PREDATEURS."""
        q = self._proba(self.t_intrus, self.vecteur(image_bgr))
        i = int(np.argmax(q))
        return PREDATEURS[i][0], float(q[i]), PREDATEURS[i][2]


class Descripteur:
    """Vecteur d'identité (MegaDescriptor) d'un poisson isolé."""

    def __init__(self):
        self.modele = None
        self._verrou = threading.Lock()

    def charger(self):
        with self._verrou:
            if self.modele is None:
                import timm
                self.appareil = appareil()
                self.modele = timm.create_model(MODELE_REID, num_classes=0, pretrained=True).to(self.appareil).eval()

    def decrire(self, poisson_bgr):
        """Tableau (1, d) : vecteur unitaire."""
        import torch
        self.charger()
        vue = cv2.cvtColor(poisson_bgr, cv2.COLOR_BGR2RGB)[None]
        x = torch.from_numpy(vue).permute(0, 3, 1, 2).float().div(255).sub(0.5).div(0.5).to(self.appareil)
        with self._verrou, torch.no_grad():
            f = self.modele(x)
        f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy()


_sift = None
_verrou_sift = threading.Lock()


def egaliser(image_bgr):
    l, a, b = cv2.split(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB))
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def points_sift(poisson_bgr):
    """Points SIFT du motif de la robe, racine-SIFT pour un appariement plus stable."""
    global _sift
    with _verrou_sift:
        if _sift is None:
            _sift = cv2.SIFT_create(nfeatures=800, contrastThreshold=0.03)
        gris = cv2.cvtColor(egaliser(poisson_bgr), cv2.COLOR_BGR2GRAY)
        masque = (cv2.absdiff(poisson_bgr, np.full_like(poisson_bgr, 128)).sum(axis=2) > 6).astype(np.uint8) * 255
        points, desc = _sift.detectAndCompute(gris, masque)
    if desc is None:
        return np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32)
    desc = np.sqrt(desc / (desc.sum(axis=1, keepdims=True) + 1e-7))
    return np.float32([p.pt for p in points]), desc.astype(np.float32)


def appariement_sift(a, b):
    """Nombre de points cohérents entre deux vues (RANSAC). Un poisson se voit des deux côtés :
    l'image miroir est essayée aussi par l'appelant."""
    pa, da = a
    pb, db = b
    if len(pa) < 8 or len(pb) < 8:
        return 0
    paires = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
    bons = [m for m, n in (p for p in paires if len(p) == 2) if m.distance < 0.8 * n.distance]
    if len(bons) < 6:
        return 0
    src = np.float32([pa[m.queryIdx] for m in bons])
    dst = np.float32([pb[m.trainIdx] for m in bons])
    _m, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=8.0)
    return int(inliers.sum()) if inliers is not None else 0
