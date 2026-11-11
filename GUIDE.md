# Guide d'utilisation de Frétille

[English version](GUIDE.en.md)

## Sommaire

1. [Installer](#1-installer)
2. [Ajouter un bac](#2-ajouter-un-bac)
3. [Placer la caméra](#3-placer-la-caméra)
4. [La surveillance au quotidien](#4-la-surveillance-au-quotidien)
5. [Santé du bac](#5-santé-du-bac)
6. [Prédateurs au bassin](#6-prédateurs-au-bassin)
7. [Mes poissons](#7-mes-poissons)
8. [Carnet de l'eau](#8-carnet-de-leau)
9. [Élevage](#9-élevage)
10. [Sur le téléphone](#10-sur-le-téléphone)
11. [Plein écran pour un direct](#11-plein-écran-pour-un-direct)
12. [Réglages](#12-réglages)
13. [Problèmes fréquents](#13-problèmes-fréquents)

---

## 1. Installer

1. Téléchargez **`Fretille-Setup.exe`** sur la [page des versions](https://github.com/Oli97430/fretille/releases/latest).
2. Double-cliquez dessus. Si Windows affiche « Windows a protégé votre ordinateur », cliquez *Informations complémentaires*, puis *Exécuter quand même*.
3. Laissez la fenêtre noire travailler : elle installe Python 3.11 s'il manque, puis télécharge environ 3 Go (5 à 30 minutes). Une carte graphique NVIDIA est utilisée si elle est présente.
4. Lancez **Frétille** depuis le raccourci du Bureau.

Relancer `Fretille-Setup.exe` répare ou met à jour l'installation, sans toucher à vos données. Pour désinstaller : Paramètres de Windows → Applications → Frétille.

Au premier lancement, les modèles de vision se téléchargent : comptez quelques minutes. En haut à droite, « Modèles prêts » indique qu'ils sont chargés.

Pour des clips en H.264 (le format accepté par Instagram et TikTok), installez aussi **ffmpeg**. Ce n'est pas obligatoire.

---

## 2. Ajouter un bac

Cliquez **＋ Bac** dans la barre des bacs.

| Champ | À quoi il sert |
|---|---|
| **Nom** | « Aquarium salon », « Bassin des koï »… |
| **Genre** | *Aquarium* (vu de côté), *Bassin* (vu de dessus, prédateurs surveillés), *Bac d'élevage* |
| **Caméra** | webcam 📷 (un aperçu de chaque webcam : cliquez sur la bonne), fenêtre ouverte 🪟 (EZVIZ Studio, Mercusys…), vidéo 🎞 ou flux `rtsp://…` |
| **Poissons dans le bac** | le nombre exact, si vous le connaissez. Laissé vide, Frétille apprend le nombre habituel |
| **Température conseillée** | la plage utilisée par le carnet de l'eau |

Ensuite, trois outils travaillent sur une image de la caméra :

- **⛶ Cadrer** : tracez la zone du bac. Le reste (meuble, mur) est ignoré.
- **〰 Ligne d'eau** (aquarium) : cliquez sur la surface de l'eau. C'est elle qui permet de voir des poissons massés sous la surface.
- **📏 Étalonner les tailles** : tracez un trait de longueur connue, par exemple une règle contre la vitre ou une pierre mesurée, et tapez sa longueur en cm. Frétille mesure alors la taille des poissons reconnus.

Chaque bac a sa pastille en haut. Sa couleur suit sa santé : vert, orange, rouge, ou gris quand il est arrêté.

---

## 3. Placer la caméra

- **Aquarium** : de face, toute la vitre dans l'image, sans reflet de fenêtre. Un éclairage stable aide.
- **Bassin** : en hauteur, vue plongeante sur l'eau et les bords (c'est là que se pose le héron).
- **Reconnaissance individuelle** : le poisson doit faire au moins 60 pixels à l'image. Plus il est grand, mieux il est reconnu.

---

## 4. La surveillance au quotidien

**▶ Démarrer** lance le bac affiché, **▶▶ Tout démarrer** lance tous les bacs.

Sur la vidéo :

- **cadre turquoise** : un poisson ;
- **cadre orange avec un nom** : un poisson reconnu ;
- **cadre rouge** : un intrus.

Cliquez sur un poisson pour le nommer, ou pour ouvrir sa fiche s'il est déjà reconnu.

**🎬 Clip 30 s** enregistre le bac maintenant, en gardant aussi les 5 secondes d'avant.

L'onglet **Événements** regroupe les prédateurs, les poissons immobiles et les autres faits notables. Un double-clic ouvre la photo ou le clip.

Tout est rangé dans le dossier des données (par défaut `Vidéos\Fretille`) :

| Dossier ou fichier | Contenu |
|---|---|
| `bacs\<bac>\mesures\` | une ligne par minute |
| `bacs\<bac>\photos\`, `clips\` | photos et clips des événements |
| `poissons\` | fiches, galeries, albums |
| `evenements.csv`, `elevage.json` | journal des événements, lots d'élevage |

---

## 5. Santé du bac

Frétille compare la dernière demi-heure aux mêmes heures des 7 jours précédents. Chaque bac a ses habitudes : des corydoras au fond, des koï qui montent quand on approche. Seul un changement compte.

| Signal | Signification probable |
|---|---|
| **Poissons en surface** (alerte) | manque d'oxygène : vérifiez le bulleur, la pompe, la température |
| **Poissons non vus** | caché, malade, sauté hors du bac, pris par un prédateur |
| **Poissons peu actifs** | eau trop froide ou polluée, maladie |
| **Poissons agités** | prédateur, bagarre, ponte, eau irritante |
| **Poissons au fond** | stress ou maladie |
| **Poisson immobile en surface** (alerte) | sans doute mort ou à l'agonie : allez voir |

Rien n'est jugé dans le noir. Le premier jour, Frétille apprend : il faut un jour de mesures avant les comparaisons.

**📊 Santé** montre la journée heure par heure : l'activité et le nombre de poissons vus, comparés à l'habitude.

---

## 6. Prédateurs au bassin

Pour un bac de genre *Bassin*, Frétille cherche aussi les oiseaux, les chats et les personnes, puis dit ce que c'est.

- **Danger** (héron, aigrette, martin-pêcheur, rapace, chat, rat, loutre, raton laveur) : photo, clip de 30 s, alerte sur le téléphone. Au plus une alerte toutes les 10 minutes par espèce.
- **Sans danger** (petit oiseau, canard, personne, chien) : cadre gris, pas d'alerte.

Cette surveillance se coupe dans **⚙ Réglages**.

---

## 7. Mes poissons

Vous pouvez reconnaître chaque poisson un par un si sa robe a un motif unique : koï, discus, bettas, poissons rouges tachés. Pour des néons tous pareils, gardez seulement le comptage.

1. Créez le poisson (**➕ Nouveau poisson**, onglet *Mes poissons*), ou cliquez dessus sur la vidéo → *Nouveau poisson*.
2. Nommez-le sur la vidéo quand il passe : il apprend alors ses vues de profil.
3. Ou ajoutez des photos dans sa fiche, onglet *Photos*, en cochant *Les apprendre aussi pour la reconnaissance*.

Sa fiche a quatre onglets :

| Onglet | Contenu |
|---|---|
| **Identité** | nom, espèce, bac, sexe, naissance (« 2022 » suffit), arrivée, origine, signes distinctifs, notes |
| **Croissance** | longueur et poids, à la main ou mesurés sur la vidéo (bac étalonné, une mesure par jour) |
| **Photos** | album souvenir, et vues apprises par la reconnaissance (clic droit : retirer une mauvaise vue) |
| **Carnet** | traitements, quarantaine, ponte, changement de bac… |

Clic droit sur un poisson : *C'est le même poisson que* regroupe deux fiches.

---

## 8. Carnet de l'eau

**💧 Eau**, pour le bac affiché : notez vos tests et vos changements d'eau. Laissez vide ce qui n'a pas été testé.

| Paramètre | Inquiétant au-dessus de | Dangereux à partir de |
|---|---|---|
| NH3/NH4 | 0,1 mg/L | 0,5 mg/L |
| NO2 | 0,1 mg/L | 0,5 mg/L |
| NO3 | 50 mg/L | 100 mg/L |
| pH | en dehors de 6 à 8,5 | — |
| Température | en dehors de la plage du bac | — |

Frétille vous rappelle de changer l'eau si le dernier changement date de 14 jours, et de refaire un test si le dernier date de 30 jours. Une valeur dangereuse déclenche une alerte sur le téléphone.

---

## 9. Élevage

**🥚 Élevage** : un lot par ponte.

1. **➕ Nouveau lot** : nom, espèce, bac, date de ponte, nombre d'œufs, parents (Ctrl+clic).
2. Notez ensuite les événements du lot :

| Événement | Effet sur l'effectif |
|---|---|
| Éclosion, comptage | fixent l'effectif |
| Pertes, vente, cession, tri | le réduisent |
| Transfert | ne le change pas, mais déplace le lot dans un autre bac |

L'âge, l'effectif et la survie se calculent seuls. La survie compte depuis l'éclosion, ventes comprises. Un clic droit sur un lot le marque terminé.

---

## 10. Sur le téléphone

**📱 Téléphone** ouvre deux réglages, à gauche et à droite de la fenêtre.

### Page web, sur le Wi-Fi de la maison

1. Cochez *Accès depuis le téléphone*.
2. Scannez le QR code avec l'appareil photo du téléphone.
3. Au premier lancement, Windows demande d'autoriser Python : cochez **Réseaux privés**.

Sur la page :

- le direct de chaque bac ;
- l'état des bacs et les derniers événements, avec photos et clips ;
- vos poissons : ajoutez une mesure, une note ou une photo prise au bord du bassin ;
- les lots : comptage, pertes, vente.

La page est protégée par un code et n'est pas visible depuis Internet. **Nouveau code** déconnecte tous les téléphones.

### Alertes, même loin de la maison

1. Cochez *Alertes sur le téléphone*.
2. Installez l'appli gratuite **ntfy**, touchez **+**, puis scannez le QR code de droite.
3. Cliquez **Envoyer un test** pour vérifier.

Vous êtes prévenu :

- d'un prédateur au bassin ;
- d'un poisson immobile en surface ;
- d'un manque d'oxygène ;
- de poissons non vus, apathiques ou agités ;
- d'une eau dangereuse ;
- d'une image figée.

Chaque alerte n'est envoyée qu'une fois par épisode. Les alertes passent par le service public ntfy.sh : le nom du sujet est la seule clé, ne le partagez pas.

---

## 11. Plein écran pour un direct

**📺 Plein écran** (ou F11) montre le bac affiché, habillé pour le public :

- « EN DIRECT », l'heure, le nombre de poissons ;
- le nom et l'âge des poissons reconnus ;
- rien de technique ni de privé.

Diffusez la fenêtre « Frétille — en direct » avec OBS.

| Touche | Effet |
|---|---|
| **P** | photo 1920×1080 dans le dossier `partage` |
| **Échap**, **F11** ou double-clic | sortir |

---

## 12. Réglages

| Réglage | Effet |
|---|---|
| **Langue** | interface, page du téléphone et alertes, en 13 langues (par défaut celle de Windows) ; redémarrez Frétille après un changement |
| **Dossier des données** | où sont rangés mesures, photos et clips |
| **Analyse toutes les … secondes** | vide = automatique : 3 s avec une carte graphique, 6 s sinon. Plus court suit mieux les poissons, mais charge plus le PC |
| **Sauvegarde quotidienne** | une archive par jour sur une clé USB ou OneDrive (les 7 dernières sont gardées, sans les clips) |
| **Reconnaître les poissons un par un**, **surveiller les prédateurs** | à couper si inutile |
| **Démarrer tous les bacs à l'ouverture**, **ouvrir Frétille au démarrage de Windows** | lancement automatique |

---

## 13. Problèmes fréquents

| Problème | Que faire |
|---|---|
| « Image figée » | le lecteur de la caméra (EZVIZ Studio, Mercusys…) a arrêté le direct : relancez-le |
| Les poissons ne sont pas tous comptés | recadrez sur le bac, évitez les reflets, rapprochez la caméra |
| Trop de « poissons non vus » | indiquez le nombre exact de poissons dans *Ce bac…* ; un poisson qui se cache souvent fausse le compte |
| Un poisson est confondu avec un autre | dans sa fiche, onglet *Photos*, retirez les mauvaises vues apprises |
| Une webcam USB ne donne pas d'image | dans *Ce bac…*, cliquez **📷** et choisissez-la sur son aperçu : son numéro n'est pas toujours celui qu'on croit. Fermez les autres programmes qui l'utilisent (Caméra, Teams, OBS) |
| La page du téléphone ne s'ouvre pas | même Wi-Fi que le PC ? Python autorisé sur les réseaux privés ? |
