"""
Frétille — langues de l'interface.

Le français est la langue source : chaque texte affiché passe par t("texte français") et la
traduction est cherchée dans langues/<code>.json ({"texte français": "traduction"}). Un texte
absent du catalogue s'affiche en français : jamais d'écran vide.

  t("Bonjour {nom}", nom="Kohaku")          texte à trous (les {noms} restent dans la traduction)
  tn("{n} poisson", "{n} poissons", n)      singulier / pluriel ; chaque forme est une clé du catalogue
  nombre(12.5)                               « 12,5 » ou « 12.5 » selon la langue
  police_pil(taille, gras)                   police PIL qui a les caractères de la langue (CJK, thaï)
  famille_tk()                               famille Tk correspondante

Langue : réglage « langue » ; par défaut celle de Windows (si elle est traduite), sinon l'anglais.
"""
import ctypes
import json
import locale
from pathlib import Path

ICI = Path(__file__).resolve().parent
DOSSIER = ICI / "langues"

LANGUES = {
    "fr": "Français", "en": "English", "es": "Español", "de": "Deutsch", "pt": "Português", "it": "Italiano",
    "nl": "Nederlands", "zh": "中文（简体）", "ja": "日本語", "ko": "한국어", "th": "ไทย", "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia",
}
VIRGULE = {"fr", "es", "de", "pt", "it", "nl", "vi", "id"}      # 12,5 ; les autres : 12.5
# polices Windows qui ont les caractères de chaque écriture (PIL) et famille Tk correspondante
POLICES = {
    "zh": (["msyh.ttc"], ["msyhbd.ttc"], "Microsoft YaHei UI"),
    "ja": (["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc"], ["YuGothB.ttc", "meiryob.ttc"], "Yu Gothic UI"),
    "ko": (["malgun.ttf"], ["malgunbd.ttf"], "Malgun Gothic"),
    "th": (["LeelawUI.ttf", "leelawui.ttf", "tahoma.ttf"], ["LeelaUIb.ttf", "leelauib.ttf", "tahomabd.ttf"], "Leelawadee UI"),
}
POLICE_DEFAUT = (["segoeui.ttf", "arial.ttf"], ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"], "Segoe UI")

langue = "fr"
_catalogue = {}
_polices = {}


def choisir(code):
    """Active une langue (code de LANGUES) ; renvoie le code retenu."""
    global langue, _catalogue
    code = code if code in LANGUES else "en"
    catalogue = {}
    if code != "fr":
        try:
            catalogue = json.loads((DOSSIER / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            catalogue = {}
    langue, _catalogue = code, catalogue
    _polices.clear()
    return code


def langue_systeme():
    """Langue de Windows si elle est traduite, sinon l'anglais (le français si Windows est en français)."""
    code = ""
    try:
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        code = locale.windows_locale.get(lcid, "")
    except (AttributeError, OSError):
        pass
    if not code:
        code = (locale.getlocale()[0] or "")
    code = code.split("_")[0].split("-")[0].lower()
    return code if code in LANGUES else "en"


def t(texte, /, **valeurs):            # « / » : un trou {texte} reste possible
    s = _catalogue.get(texte) or texte
    if valeurs:
        try:
            return s.format(**valeurs)
        except (KeyError, IndexError, ValueError):      # traduction abîmée : le français, plutôt qu'une erreur
            return texte.format(**valeurs)
    return s


def pluriel(n):
    """Règle du singulier : en français 0 et 1 ; en anglais, allemand… 1 seulement."""
    if langue == "fr":
        return abs(n) < 2
    return n == 1


def tn(singulier, pluriel_, n, /, **valeurs):
    return t(singulier if pluriel(n) else pluriel_, n=n, **valeurs)


def nombre(v, decimales=1):
    """Nombre court : 12,5 ou 12.5 ; zéros inutiles retirés."""
    if v is None:
        return ""
    s = f"{v:.{decimales}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",") if langue in VIRGULE else s


def pourcent(v):
    """« 70 % » (français, espace insécable) ou « 70% »."""
    return f"{v:.0%}".replace("%", " %") if langue == "fr" else f"{v:.0%}"


def police_pil(taille, gras=False):
    """Police PIL de la langue (CJK, thaï…), mise en cache."""
    from PIL import ImageFont
    cle = (taille, gras)
    if cle in _polices:
        return _polices[cle]
    normales, grasses, _f = POLICES.get(langue, POLICE_DEFAUT)
    for nom in (grasses if gras else []) + normales + POLICE_DEFAUT[1 if gras else 0] + POLICE_DEFAUT[0]:
        try:
            _polices[cle] = ImageFont.truetype(nom, taille)
            return _polices[cle]
        except OSError:
            continue
    _polices[cle] = ImageFont.load_default()
    return _polices[cle]


def famille_tk():
    return POLICES.get(langue, POLICE_DEFAUT)[2]


def catalogue_web(cles):
    """Traductions des textes de la page du téléphone : {clé française: texte affiché}."""
    return {c: t(c) for c in cles}
