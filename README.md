# Frétille

**The aquarium that watches over its fish.** One camera per tank (aquarium, pond, breeding tank). Frétille keeps watch for you, day and night, on a Windows PC.

🌍 English · [Français](README.fr.md) · Español · Deutsch · Português · Italiano · Nederlands · 中文 · 日本語 · 한국어 · ไทย · Tiếng Việt · Bahasa Indonesia. The app, the phone page and the alerts are available in all 13 languages. Frétille follows the Windows language, and you can change it in **⚙ Settings**.

## What it does

- **Tank health**: number of fish, activity, fish crowding under the surface (lack of oxygen), staying on the bottom, lethargic or agitated. Each tank is compared with its own habits.
- **Motionless fish**: a fish still at the surface for 10 minutes triggers an alert. Elsewhere it is only noted, because some fish sleep on the bottom.
- **Pond predators**: heron, egret, kingfisher, cat, bird of prey… Frétille takes a photo, records a clip and alerts you.
- **Individual fish recognition**: koi, discus, bettas, spotted goldfish. Each fish gets its own record: identity, growth measured on video, photos and log.
- **Water log**: pH, NH3, NO2, NO3, GH, KH, temperature and water changes. Dangerous values are flagged.
- **Breeding**: fry batches with spawning date, parents, hatching, losses, sales and transfers. Frétille keeps the head count and survival rate.
- **On your phone**:
  - a web page on your home Wi-Fi, protected by a code: live video, tank status, add a measurement or a photo from the pond side;
  - alerts anywhere, even away from home, with the free ntfy app.
- **Full screen** to stream a tank live (YouTube…), showing the names of recognised fish.

## Install

1. Install Python 3.11 from python.org.
2. Double-click `logiciel\installer.bat`. It downloads about 3 GB, once.
3. Run `logiciel\Fretille.bat`.

The vision models (Grounding DINO, BioCLIP, MegaDescriptor) download on first launch.

User guide: [GUIDE.en.md](GUIDE.en.md) (English) · [GUIDE.md](GUIDE.md) (français).

## Model licences

| Model | Role | Licence |
|---|---|---|
| Grounding DINO tiny | detection | Apache 2.0 |
| BioCLIP | species and intruders | MIT |
| MegaDescriptor | individual recognition | CC BY-NC 4.0, non-commercial use |

Frétille is a sibling of [Broutille](https://github.com/Oli97430/broutille), the tortoise feeder that recognises its tortoises.
