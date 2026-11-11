"""Identité d'un poisson : dates partielles (naissance souvent connue à l'année près), âge lisible,
résumé d'une ligne. Fonctions pures, sans interface, testées dans tests/test_fiches.py.

Champs d'une fiche du registre (tous facultatifs sauf nom et espèce) :
  sexe        "Mâle" | "Femelle" | "" (inconnu)
  naissance   "AAAA", "AAAA-MM" ou "AAAA-MM-JJ"
  arrivee     même format : arrivée à la maison
  origine     texte libre (éleveur, refuge, née ici…)
  signes      signes distinctifs (écaille abîmée, marque…)
  notes       texte libre
  profil      nom de la photo de profil (album/<nom> ou <nom> de la galerie)
  carnet      [{"date": "AAAA-MM-JJ", "type": "Soin", "texte": "..."}] par ordre de date
"""
from datetime import date

from i18n import t, tn

SEXES = ("", "Mâle", "Femelle")
TYPES_CARNET = ("Note", "Traitement", "Quarantaine", "Blessure", "Maladie", "Ponte", "Reproduction", "Changement de bac", "Vente / cession")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre")


def nom_mois(numero):
    """Nom du mois (1-12) dans la langue de l'interface (MOIS reste en français)."""
    return (t("janvier"), t("février"), t("mars"), t("avril"), t("mai"), t("juin"), t("juillet"), t("août"),
            t("septembre"), t("octobre"), t("novembre"), t("décembre"))[numero - 1]


def _cles_a_traduire():
    """Jamais appelée : signale à tests/cles_i18n.py les valeurs stockées en français et traduites à
    l'affichage par t(valeur) (sexe, types de carnet)."""
    return (t("Mâle"), t("Femelle"), t("Note"), t("Traitement"), t("Quarantaine"), t("Blessure"), t("Maladie"),
            t("Ponte"), t("Reproduction"), t("Changement de bac"), t("Vente / cession"))


def lire_date_partielle(texte, aujourd_hui=None):
    """« 2019 », « 03/2019 », « 15/03/2019 » ou ISO → "AAAA", "AAAA-MM" ou "AAAA-MM-JJ".
    Chaîne vide → "". ValueError si la date est impossible ou dans le futur."""
    propre = (texte or "").strip().replace(".", "/").replace("-", "/").replace(" ", "/")
    morceaux = [m for m in propre.split("/") if m]           # « 15 03 2019 » comme « 15/03/2019 »
    if not morceaux:
        return ""
    if len(morceaux[0]) == 4:            # ISO : AAAA/MM/JJ
        morceaux = morceaux[::-1]
    if not all(m.isdigit() for m in morceaux) or len(morceaux) > 3:
        raise ValueError(t("date illisible : « {texte} »", texte=texte))
    annee = int(morceaux[-1])
    aujourd_hui = aujourd_hui or date.today()
    if annee < 100:                      # « 03/19 » → 2019, « 03/99 » → 1999
        annee += 2000 if annee + 2000 <= aujourd_hui.year else 1900
    mois = int(morceaux[-2]) if len(morceaux) >= 2 else None
    jour = int(morceaux[-3]) if len(morceaux) == 3 else None
    aujourd_hui = aujourd_hui or date.today()
    if not 1900 <= annee <= aujourd_hui.year:
        raise ValueError(t("année impossible : {annee}", annee=annee))
    if mois is None:
        return f"{annee:04d}"
    if not 1 <= mois <= 12:
        raise ValueError(t("mois impossible : {mois}", mois=mois))
    if jour is None:
        if (annee, mois) > (aujourd_hui.year, aujourd_hui.month):
            raise ValueError(t("date dans le futur"))
        return f"{annee:04d}-{mois:02d}"
    try:
        d = date(annee, mois, jour)       # le 31/02
    except ValueError:
        raise ValueError(t("jour impossible : {jour}", jour=jour)) from None
    if d > aujourd_hui:
        raise ValueError(t("date dans le futur"))
    return d.isoformat()


def date_lisible(valeur):
    """"2019" → "2019" ; "2019-03" → "mars 2019" ; "2019-03-15" → "15/03/2019" ; "" → ""."""
    if not valeur:
        return ""
    p = valeur.split("-")
    if len(p) == 1:
        return p[0]
    if len(p) == 2:
        return t("{mois} {annee}", mois=nom_mois(int(p[1])), annee=p[0])
    return t("{jour}/{mois}/{annee}", jour=p[2], mois=p[1], annee=p[0])


def date_courte(valeur):
    """"2026-11-10" → jour et mois seuls, dans l'ordre de la langue (« 10/11 », « 11/10 », « 10.11. »)."""
    if not valeur or len(valeur) < 10:
        return valeur or ""
    return t("{jour}/{mois}", jour=valeur[8:10], mois=valeur[5:7])


def date_saisie(valeur):
    """Valeur stockée → texte à remettre dans le champ de saisie."""
    if not valeur:
        return ""
    p = valeur.split("-")
    return "/".join(reversed(p))


def age_mois(naissance, aujourd_hui=None):
    """Âge en mois entiers (date partielle : on compte depuis le milieu de la période ;
    année en cours sans mois : depuis janvier, pour ne pas tomber en dessous de zéro)."""
    if not naissance:
        return None
    aujourd_hui = aujourd_hui or date.today()
    p = [int(x) for x in naissance.split("-")]
    mois_defaut = 7 if p[0] < aujourd_hui.year else 1
    annee, mois, jour = p[0], (p[1] if len(p) > 1 else mois_defaut), (p[2] if len(p) > 2 else 15)
    m = (aujourd_hui.year - annee) * 12 + aujourd_hui.month - mois - (1 if aujourd_hui.day < jour else 0)
    return max(0, m)


def age_lisible(naissance, aujourd_hui=None):
    """"7 ans", "1 an et 3 mois", "5 mois", "moins d'un mois" ; "environ" si seule l'année est connue."""
    m = age_mois(naissance, aujourd_hui)
    if m is None:
        return ""
    environ = len(naissance) == 4
    # « mois » est invariable : le singulier a sa propre clé (« 1 mois », reste >= 1) pour que les
    # autres langues puissent l'accorder (1 month / 2 months)
    ans, reste = divmod(m, 12)
    if ans == 0:
        texte = tn("1 mois", "{n} mois", reste) if reste else t("moins d'un mois")
    else:
        texte = tn("{n} an", "{n} ans", ans)
        if reste and ans < 3 and not environ:   # les mois ne comptent plus passé trois ans
            texte = t("{ans} et {mois}", ans=texte, mois=tn("1 mois", "{n} mois", reste))
    return t("environ {age}", age=texte) if environ else texte


def resume_identite(fiche, aujourd_hui=None):
    """Une ligne : « Koï · Femelle · 3 ans (née en 2023) »."""
    espece = fiche.get("espece", "")
    morceaux = [t(espece) if espece else ""]
    sexe = fiche.get("sexe", "")
    if sexe:
        morceaux.append(t(sexe))
    if fiche.get("naissance"):
        quand = date_lisible(fiche["naissance"])
        age = age_lisible(fiche["naissance"], aujourd_hui)
        if len(fiche["naissance"]) == 10:
            texte = (t("{age} (né le {quand})", age=age, quand=quand) if sexe == "Mâle"
                     else t("{age} (née le {quand})", age=age, quand=quand))
        else:
            texte = (t("{age} (né en {quand})", age=age, quand=quand) if sexe == "Mâle"
                     else t("{age} (née en {quand})", age=age, quand=quand))
        morceaux.append(texte)
    return "  ·  ".join(m for m in morceaux if m)
