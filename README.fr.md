# Frétille

**L'aquarium qui veille sur ses poissons.** Une caméra par bac (aquarium, bassin, bac d'élevage), et Frétille surveille pour vous, jour et nuit, sur un PC Windows.

## Ce qu'il fait

- **Santé du bac** : nombre de poissons, activité, poissons en surface (manque d'oxygène), au fond, apathiques ou agités. Chaque bac est comparé à ses propres habitudes.
- **Poisson immobile** : en surface pendant 10 minutes, c'est une alerte. Ailleurs, une simple note, car certains poissons dorment au fond.
- **Prédateurs au bassin** : héron, aigrette, martin-pêcheur, chat, rapace… Frétille prend une photo, enregistre un clip et vous alerte.
- **Poissons reconnus un par un** : koï, discus, bettas, poissons rouges tachés. Chaque poisson a sa fiche (identité, croissance mesurée sur la vidéo, photos, carnet).
- **Carnet de l'eau** : pH, NH3, NO2, NO3, GH, KH, température et changements d'eau. Frétille signale les valeurs dangereuses.
- **Élevage** : lots d'alevins (ponte, parents, éclosion, pertes, ventes, transferts), avec l'effectif et le taux de survie.
- **Sur le téléphone** :
  - une page web sur le Wi-Fi de la maison, protégée par un code : le direct, l'état des bacs, une mesure ou une photo prise au bord du bassin ;
  - des alertes partout, même loin de la maison, avec l'appli gratuite ntfy.
- **Plein écran** pour diffuser le bac en direct (YouTube…), avec le nom des poissons reconnus.

## Installer

1. Installez Python 3.11 (python.org).
2. Double-cliquez `logiciel\installer.bat`. Il télécharge environ 3 Go, une seule fois.
3. Lancez `logiciel\Fretille.bat`.

Les modèles de vision (Grounding DINO, BioCLIP, MegaDescriptor) se téléchargent au premier lancement.

Le mode d'emploi complet est dans [GUIDE.md](GUIDE.md) (English : [GUIDE.en.md](GUIDE.en.md)).

## Licences des modèles

| Modèle | Rôle | Licence |
|---|---|---|
| Grounding DINO tiny | détection | Apache 2.0 |
| BioCLIP | espèces et intrus | MIT |
| MegaDescriptor | reconnaissance individuelle | CC BY-NC 4.0, usage non commercial |

🌍 [English](README.md) · Français, et 11 autres langues dans l'appli.

Frétille est un projet frère de [Broutille](https://github.com/Oli97430/broutille), la mangeoire qui reconnaît ses tortues.
