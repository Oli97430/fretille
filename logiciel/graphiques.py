"""
Frétille — graphiques en images PIL (sans bibliothèque de graphiques) : courbe de croissance,
activité de la journée comparée à l'habitude, petite ligne d'activité (sparkline).
"""
from datetime import date

from PIL import Image, ImageDraw

from i18n import nombre, police_pil, t

FOND, ENCRE, DISCRET, FILET = (19, 34, 42), (230, 241, 243), (138, 165, 173), (31, 53, 64)
ACCENT, AMBRE, ROUGE = (60, 198, 192), (240, 180, 76), (248, 81, 73)


def police(taille, gras=False):
    """Police qui a les caractères de la langue de l'interface (CJK, thaï…)."""
    return police_pil(taille, gras)


def _pas(etendue, cible=5):
    for p in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000):
        if etendue / p <= cible:
            return p
    return etendue / cible


def _nombre(v):
    return nombre(v, 2)


def courbe(points, unite="cm", titre="", taille=(900, 300), couleur=ACCENT):
    """Courbe datée : points [(date ISO, valeur)] ; l'axe du temps est à l'échelle."""
    l, h = taille
    img = Image.new("RGB", taille, FOND)
    d = ImageDraw.Draw(img)
    f_titre, f_axe = police(max(14, h // 18), True), police(max(11, h // 24))
    haut = 0
    if titre:
        d.text((0, 0), titre, font=f_titre, fill=ENCRE)
        haut = int(f_titre.size * 1.6)
    if not points:
        d.text((l // 2, h // 2), t("Pas encore de mesure"), font=f_axe, fill=DISCRET, anchor="mm")
        return img
    points = sorted(points)
    jours = [date.fromisoformat(p[0]) for p in points]
    valeurs = [p[1] for p in points]
    gauche, bas = int(f_axe.size * 5), int(f_axe.size * 2.4)
    x0, y0, x1, y1 = gauche, haut + 8, l - 20, h - bas
    bas_v, haut_v = min(valeurs), max(valeurs)
    marge = max((haut_v - bas_v) * 0.15, abs(haut_v) * 0.05, 0.5)
    bas_v, haut_v = max(0, bas_v - marge), haut_v + marge
    etendue = haut_v - bas_v
    pas = _pas(etendue)
    v = pas * int(bas_v / pas)
    while v <= haut_v:
        if v >= bas_v:
            y = y1 - (y1 - y0) * (v - bas_v) / etendue
            d.line([(x0, y), (x1, y)], fill=FILET)
            d.text((x0 - 8, y), f"{_nombre(round(v, 2))} {unite}", font=f_axe, fill=DISCRET, anchor="rm")
        v += pas
    j0 = jours[0]
    duree = max((jours[-1] - j0).days, 30)

    def xy(j, val):
        return x0 + (x1 - x0) * (j - j0).days / duree, y1 - (y1 - y0) * (val - bas_v) / etendue

    for j in (jours[0], jours[-1]) if len(jours) > 1 else (jours[0],):
        d.text((xy(j, bas_v)[0], y1 + 6), t("{jour}/{mois}/{annee}", jour=f"{j:%d}", mois=f"{j:%m}", annee=f"{j:%y}"), font=f_axe, fill=DISCRET, anchor="ma")
    coords = [xy(j, val) for j, val in zip(jours, valeurs)]
    if len(coords) > 1:
        d.line(coords, fill=couleur, width=3, joint="curve")
    for x, y in coords:
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=couleur)
    return img


def journee(aujourd_hui, habitude=(), titre="", taille=(900, 260), unite="", echelle=1.0):
    """Barres par heure (aujourd'hui) et trait de l'habitude : séries [(heure 0-23, valeur)]."""
    l, h = taille
    img = Image.new("RGB", taille, FOND)
    d = ImageDraw.Draw(img)
    f_titre, f_axe = police(max(14, h // 16), True), police(max(11, h // 22))
    haut = 0
    if titre:
        d.text((0, 0), titre, font=f_titre, fill=ENCRE)
        haut = int(f_titre.size * 1.6)
    x0, y0, x1, y1 = 8, haut + 6, l - 8, h - int(f_axe.size * 2)
    maxi = max([v for _h, v in aujourd_hui] + [v for _h, v in habitude] + [1e-9]) * 1.1
    largeur = (x1 - x0) / 24
    for heure in range(0, 24, 3):
        d.text((x0 + largeur * (heure + 0.5), y1 + 4), t("{h} h", h=heure), font=f_axe, fill=DISCRET, anchor="ma")
    for heure, v in aujourd_hui:
        y = y1 - (y1 - y0) * v / maxi
        d.rectangle([x0 + largeur * heure + 2, y, x0 + largeur * (heure + 1) - 2, y1], fill=ACCENT)
    if habitude:
        pts = [(x0 + largeur * (heure + 0.5), y1 - (y1 - y0) * v / maxi) for heure, v in habitude]
        if len(pts) > 1:
            d.line(pts, fill=AMBRE, width=3)
        for x, y in pts:
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=AMBRE)
    d.line([(x0, y1), (x1, y1)], fill=FILET, width=2)
    return img


def petite_ligne(valeurs, taille=(380, 46), couleur=ACCENT):
    """Sparkline des dernières valeurs (activité des deux dernières heures)."""
    l, h = taille
    img = Image.new("RGB", taille, FOND)
    if len(valeurs) < 2:
        return img
    d = ImageDraw.Draw(img)
    maxi = max(max(valeurs), 1e-9)
    pts = [(i * (l - 1) / (len(valeurs) - 1), h - 3 - (h - 6) * v / maxi) for i, v in enumerate(valeurs)]
    d.polygon(pts + [(l - 1, h), (0, h)], fill=(30, 70, 76))
    d.line(pts, fill=couleur, width=2)
    return img
