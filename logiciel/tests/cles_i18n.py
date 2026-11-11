"""Liste les textes à traduire (appels t(...) / tn(...) en Python, T("...") dans la page du téléphone)
et vérifie les catalogues langues/*.json : clés manquantes, trous {…} différents.

  python tests/cles_i18n.py            vérifie (code de sortie 1 si un catalogue est incomplet)
  python tests/cles_i18n.py --liste    écrit langues/_cles.json (clés à traduire, avec le fichier d'origine)
"""
import ast
import json
import re
import string
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ICI))
import i18n  # noqa: E402

FICHIERS = sorted(p for p in ICI.glob("*.py") if p.name not in ("i18n.py", "creer_logo.py"))
JS = re.compile(r"""\bT\(\s*(["'])((?:\\.|(?!\1).)*)\1""")


def cles():
    trouvees = {}
    for p in FICHIERS:
        source = p.read_text(encoding="utf-8")
        arbre = ast.parse(source)
        for n in ast.walk(arbre):
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute)):
                nom = n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                if nom in ("t", "tn", "T"):
                    nb = 2 if nom == "tn" else 1
                    for a in n.args[:nb]:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            trouvees.setdefault(a.value, p.name)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "T(" in n.value:
                for m in JS.finditer(n.value):
                    texte = m.group(2).encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
                    trouvees.setdefault(texte, p.name)
    return trouvees


def trous(texte):
    return sorted({f for _l, f, _s, _c in string.Formatter().parse(texte) if f})


def verifier():
    toutes = cles()
    erreurs = 0
    for code in i18n.LANGUES:
        if code == "fr":
            continue
        chemin = i18n.DOSSIER / f"{code}.json"
        try:
            cat = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"ÉCHEC {code} : catalogue illisible ({e})")
            erreurs += 1
            continue
        manquantes = [c for c in toutes if not cat.get(c)]
        mauvais_trous = [c for c in toutes if cat.get(c) and trous(cat[c]) != trous(c)]
        if manquantes or mauvais_trous:
            erreurs += 1
            print(f"ÉCHEC {code} : {len(manquantes)} manquantes, {len(mauvais_trous)} trous différents "
                  f"{(manquantes + mauvais_trous)[:3]}")
        else:
            print(f"OK   {code} : {len(toutes)} textes")
    return erreurs


if __name__ == "__main__":
    if "--liste" in sys.argv:
        toutes = cles()
        i18n.DOSSIER.mkdir(exist_ok=True)
        (i18n.DOSSIER / "_cles.json").write_text(json.dumps(toutes, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(toutes)} textes à traduire -> langues/_cles.json")
    else:
        sys.exit(1 if verifier() else 0)
