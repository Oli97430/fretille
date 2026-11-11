# Frétille user guide

[Version française](GUIDE.md)

## Contents

1. [Install](#1-install)
2. [Language](#2-language)
3. [Add a tank](#3-add-a-tank)
4. [Place the camera](#4-place-the-camera)
5. [Everyday monitoring](#5-everyday-monitoring)
6. [Tank health](#6-tank-health)
7. [Pond predators](#7-pond-predators)
8. [My fish](#8-my-fish)
9. [Water log](#9-water-log)
10. [Breeding](#10-breeding)
11. [On your phone](#11-on-your-phone)
12. [Full screen for a live stream](#12-full-screen-for-a-live-stream)
13. [Settings](#13-settings)
14. [Common problems](#14-common-problems)

---

## 1. Install

1. Download **`Fretille-Setup.exe`** from the [releases page](https://github.com/Oli97430/fretille/releases/latest).
2. Double-click it. If Windows shows "Windows protected your PC", click *More info*, then *Run anyway*.
3. Let the black window work: it installs Python 3.11 if missing, then downloads about 3 GB (5 to 30 minutes). An NVIDIA graphics card is used if present.
4. Start **Frétille** from the desktop shortcut.

Running `Fretille-Setup.exe` again repairs or updates the installation without touching your data. To uninstall: Windows Settings → Apps → Frétille.

On first launch the vision models download, which takes a few minutes. "Models ready", top right, means they are loaded.

For clips in H.264 (the format Instagram and TikTok accept), also install **ffmpeg**. This is optional.

---

## 2. Language

Frétille follows the Windows language when it is one of the 13 available:

- English, Français, Español, Deutsch, Português, Italiano, Nederlands;
- 中文, 日本語, 한국어, ไทย;
- Tiếng Việt, Bahasa Indonesia.

Otherwise it uses English. To choose another one, go to **⚙ Settings → Language**, then restart Frétille. The phone page and the alerts use the same language.

---

## 3. Add a tank

Click **＋ Tank** in the tank bar.

| Field | Purpose |
|---|---|
| **Name** | "Living-room aquarium", "Koi pond"… |
| **Type** | *Aquarium* (side view), *Pond* (top view, predators watched), *Breeding tank* |
| **Camera** | webcam 📷 (a preview of each webcam: click the right one), open window 🪟 (EZVIZ Studio, Mercusys…), video file 🎞 or an `rtsp://…` stream |
| **Fish in the tank** | the exact number, if you know it. Left empty, Frétille learns the usual number |
| **Recommended temperature** | the range used by the water log |

Three tools then work on a camera image:

- **⛶ Crop**: draw the tank area. Everything else (furniture, wall) is ignored.
- **〰 Water line** (aquarium): click on the water surface. This is what lets Frétille see fish crowding under the surface.
- **📏 Calibrate sizes**: draw a line of known length, for example a ruler against the glass or a measured stone, and type its length in cm. Frétille then measures recognised fish.

Each tank has its own pill at the top. Its colour follows the tank's health: green, orange, red, or grey when it is stopped.

---

## 4. Place the camera

- **Aquarium**: from the front, the whole glass in view, with no window reflection. Steady lighting helps.
- **Pond**: high up, looking down on the water and the edges (where herons land).
- **Individual recognition**: the fish must be at least 60 pixels on screen. The bigger it is, the better it is recognised.

---

## 5. Everyday monitoring

**▶ Start** monitors the tank shown, **▶▶ Start all** monitors every tank.

On the video:

- **teal box**: a fish;
- **orange box with a name**: a recognised fish;
- **red box**: an intruder.

Click a fish to name it, or to open its record if it is already recognised.

**🎬 Clip 30 s** records the tank now, including the 5 seconds before.

The **Events** tab gathers predators, motionless fish and other noteworthy events. Double-click one to open its photo or clip.

Everything is stored in the data folder (by default `Videos\Fretille`):

| Folder or file | Contents |
|---|---|
| `bacs\<tank>\mesures\` | one line per minute |
| `bacs\<tank>\photos\`, `clips\` | event photos and clips |
| `poissons\` | fish records, galleries, albums |
| `evenements.csv`, `elevage.json` | event log, breeding batches |

---

## 6. Tank health

Frétille compares the last half hour with the same hours over the previous 7 days. Every tank has its own habits: corydoras on the bottom, koi rising when someone comes near. Only a change counts.

| Signal | Likely meaning |
|---|---|
| **Fish at the surface** (alert) | lack of oxygen: check the air pump, the filter and the temperature |
| **Fish not seen** | hiding, sick, jumped out of the tank, taken by a predator |
| **Lethargic fish** | water too cold or polluted, disease |
| **Agitated fish** | predator, fight, spawning, irritating water |
| **Fish on the bottom** | stress or disease |
| **Motionless fish at the surface** (alert) | probably dead or dying: go and check |

Nothing is judged in the dark. On the first day Frétille learns: it needs one day of measurements before it can compare.

**📊 Health** shows the day hour by hour: activity and fish seen, compared with the usual.

---

## 7. Pond predators

For a *Pond* tank, Frétille also looks for birds, cats and people, then identifies them.

- **Danger** (heron, egret, kingfisher, bird of prey, cat, rat, otter, raccoon): photo, 30-second clip, alert on your phone. At most one alert every 10 minutes per species.
- **Harmless** (small bird, duck, person, dog): grey box, no alert.

You can switch this off in **⚙ Settings**.

---

## 8. My fish

Individual recognition works for fish with a unique pattern: koi, discus, bettas, spotted goldfish. For identical neon tetras, keep to counting.

1. Create the fish (**➕ New fish**, *My fish* tab), or click it on the video → *New fish*.
2. Name it on the video as it swims by: it then learns its side views.
3. Or add photos in its record, *Photos* tab, ticking *Also learn them for recognition*.

Its record has four tabs:

| Tab | Contents |
|---|---|
| **Identity** | name, species, tank, sex, birth ("2022" is enough), arrival, origin, distinguishing marks, notes |
| **Growth** | length and weight, by hand or measured on video (calibrated tank, one measurement per day) |
| **Photos** | keepsake album, and views learned for recognition (right-click: remove a wrong view) |
| **Log** | treatments, quarantine, spawning, tank change… |

Right-click a fish: *Same fish as* merges two records.

---

## 9. Water log

**💧 Water**, for the tank shown: log your tests and water changes. Leave blank whatever you did not test.

| Parameter | Worrying above | Dangerous from |
|---|---|---|
| NH3/NH4 | 0.1 mg/L | 0.5 mg/L |
| NO2 | 0.1 mg/L | 0.5 mg/L |
| NO3 | 50 mg/L | 100 mg/L |
| pH | outside 6 to 8.5 | — |
| Temperature | outside the tank's range | — |

Frétille reminds you to change the water if the last change was 14 days ago, and to test again if the last test was 30 days ago. A dangerous value triggers an alert on your phone.

---

## 10. Breeding

**🥚 Breeding**: one batch per spawn.

1. **➕ New batch**: name, species, tank, spawning date, number of eggs, parents (Ctrl+click).
2. Then log the batch's events:

| Event | Effect on the head count |
|---|---|
| Hatching, count | set the head count |
| Losses, sale, given away, culling | reduce it |
| Transfer | does not change it, but moves the batch to another tank |

Age, head count and survival are worked out automatically. Survival counts from hatching, sales included; culled fry count neither as survivors nor as deaths. Right-click a batch to mark it finished.

---

## 11. On your phone

**📱 Phone** opens two settings, on the left and right of the window.

### Web page, on your home Wi-Fi

1. Tick *Access from the phone*.
2. Scan the QR code with the phone's camera.
3. On first launch, Windows asks to allow Python: tick **Private networks**.

On the page:

- live video of every tank;
- tank status and latest events, with photos and clips;
- your fish: add a measurement, a note or a photo taken at the pond;
- batches: count, losses, sale.

The page is protected by a code and is not visible from the Internet. **New code** disconnects every phone.

### Alerts, even away from home

1. Tick *Alerts on the phone*.
2. Install the free **ntfy** app, tap **+**, then scan the QR code on the right.
3. Click **Send a test** to check.

You are warned about:

- a predator at the pond;
- a fish motionless at the surface;
- a lack of oxygen;
- fish not seen, lethargic or agitated;
- dangerous water;
- a frozen image.

Each alert is sent only once per episode. Alerts go through the public ntfy.sh service: the topic name is the only key, so don't share it.

---

## 12. Full screen for a live stream

**📺 Full screen** (or F11) shows the tank, dressed up for an audience:

- "LIVE", the time, the number of fish;
- the name and age of recognised fish;
- nothing technical or private.

Stream the "Frétille — live" window with OBS.

| Key | Effect |
|---|---|
| **P** | 1920×1080 photo in the `partage` folder |
| **Esc**, **F11** or double-click | exit |

---

## 13. Settings

| Setting | Effect |
|---|---|
| **Language** | interface, phone page and alerts; restart Frétille after a change |
| **Data folder** | where measurements, photos and clips are stored |
| **Analyse every … seconds** | empty = automatic: 3 s with a graphics card, 6 s otherwise. Shorter tracks fish better but loads the PC more |
| **Daily backup** | one archive per day on a USB stick or OneDrive (the last 7 are kept, without clips) |
| **Recognise fish individually**, **watch for predators** | switch off if not needed |
| **Start all tanks on opening**, **open Frétille when Windows starts** | automatic start |

---

## 14. Common problems

| Problem | What to do |
|---|---|
| "Frozen image" | the camera viewer (EZVIZ Studio, Mercusys…) stopped the live view: restart it |
| Not every fish is counted | crop to the tank, avoid reflections, move the camera closer |
| Too many "fish not seen" | enter the exact number of fish in *This tank…*; a fish that often hides skews the count |
| A fish is mistaken for another | in its record, *Photos* tab, remove the wrong learned views |
| A USB webcam gives no picture | in *This tank…*, click **📷** and pick it from its preview: its number is not always the one you expect. Close other programs using it (Camera, Teams, OBS) |
| The phone page won't open | same Wi-Fi as the PC? Python allowed on private networks? |
| Asian characters show as squares | Windows needs its language fonts (Settings → Time & language → Language → add the language) |
