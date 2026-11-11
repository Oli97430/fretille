"""
Frétille — sources vidéo : webcam USB, fenêtre ouverte, flux Internet ou fichier vidéo.

  - webcam           : « 0 », « 1 »...
  - fenêtre ouverte  : « fenetre:EZVIZ Studio » (une partie du titre suffit)
  - flux Internet    : rtsp://..., http(s)://...m3u8, ou page d'une caméra en direct (YouTube, Twitch...)
  - fichier vidéo    : chemin du fichier (analyse a posteriori)

Une source s'utilise comme un cv2.VideoCapture : read(), get(), release().
"""

import ctypes
import ctypes.wintypes as wt
import os
import time
import urllib.parse
from pathlib import Path

import cv2
import numpy as np

from i18n import t

PREFIXE_FENETRE = "fenetre:"
IMAGES_PAR_SECONDE_FENETRE = 10
# RTSP en TCP : en UDP, les caméras Wi-Fi perdent des paquets et l'image se déchire
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;10000000")


def source_depuis_texte(texte):
    texte = str(texte).strip()
    return int(texte) if texte.isdigit() else texte


def est_flux(source):
    return isinstance(source, str) and "://" in source


def est_fenetre(source):
    return isinstance(source, str) and source.startswith(PREFIXE_FENETRE)


def est_fichier(source):
    return isinstance(source, str) and not est_flux(source) and not est_fenetre(source) and Path(source).is_file()


def masquer(source):
    """Adresse sans son mot de passe : elle s'affiche à l'écran et dans les messages."""
    if not est_flux(source):
        return str(source)
    try:
        u = urllib.parse.urlsplit(source)
        mot = u.password
    except ValueError:                  # adresse malformée : on ne montre rien de risqué
        return source.split("?", 1)[0].split("@")[-1]
    if mot:
        source = source.replace(f":{mot}@", ":•••@")
    if u.query:                         # caméras IP : ?user=admin&pwd=…
        morceaux = []
        for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True):
            morceaux.append(f"{k}={'•••' if k.lower() in ('pwd', 'pass', 'passwd', 'password', 'mdp', 'token', 'key') else v}")
        source = source.split("?", 1)[0] + "?" + "&".join(morceaux)
    return source


# ------------------------------------------------------------------
#  Flux Internet
# ------------------------------------------------------------------
def resoudre_flux(source):
    """Page web d'une caméra en direct (YouTube, Twitch...) -> adresse du flux, avec yt-dlp.
    À refaire à chaque connexion : ces adresses expirent au bout de quelques heures.
    Un flux direct (.m3u8, rtsp...) ou une page inconnue de yt-dlp est rendu tel quel."""
    if not source.startswith("http"):
        return source
    if Path(urllib.parse.urlparse(source).path).suffix.lower() in (".m3u8", ".mpd", ".mp4", ".ts", ".flv", ".mjpg", ".mjpeg"):
        return source
    try:
        import yt_dlp
        options = {"quiet": True, "no_warnings": True, "noplaylist": True,
                   "format": "bestvideo[height<=1080]/best[height<=1080]/best"}   # le son n'est pas utilisé
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(source, download=False)["url"]
    except Exception:
        return source


# ------------------------------------------------------------------
#  Fenêtre ouverte (API Windows, sans bibliothèque en plus)
# ------------------------------------------------------------------
user32, gdi32 = (ctypes.WinDLL(n, use_last_error=True) for n in ("user32", "gdi32")) if os.name == "nt" else (None, None)
PW_RENDERFULLCONTENT = 2        # capture aussi le contenu dessiné par la carte graphique (lecteurs vidéo)
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


if user32:
    user32.GetDC.restype = wt.HANDLE
    user32.GetDC.argtypes = [wt.HWND]
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HANDLE]
    user32.PrintWindow.argtypes = [wt.HWND, wt.HANDLE, wt.UINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.ChildWindowFromPointEx.restype = wt.HWND
    user32.ChildWindowFromPointEx.argtypes = [wt.HWND, wt.POINT, wt.UINT]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    gdi32.CreateCompatibleDC.restype = wt.HANDLE
    gdi32.CreateCompatibleDC.argtypes = [wt.HANDLE]
    gdi32.CreateCompatibleBitmap.restype = wt.HANDLE
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wt.HANDLE
    gdi32.SelectObject.argtypes = [wt.HANDLE, wt.HANDLE]
    gdi32.DeleteObject.argtypes = [wt.HANDLE]
    gdi32.DeleteDC.argtypes = [wt.HANDLE]
    gdi32.BitBlt.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HANDLE, ctypes.c_int,
                             ctypes.c_int, wt.DWORD]
    gdi32.GetDIBits.argtypes = [wt.HANDLE, wt.HANDLE, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]


def lister_fenetres():
    """[(titre, poignée)] des fenêtres ouvertes et visibles."""
    trouvees = []
    rappel = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def voir(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                tampon = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, tampon, n + 1)
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 100 and r.bottom - r.top > 100 and tampon.value not in ("Program Manager",):
                    trouvees.append((tampon.value, hwnd))
        return True

    user32.EnumWindows(rappel(voir), 0)
    return trouvees


def trouver_fenetre(titre):
    titre = titre.lower()
    for nom, hwnd in lister_fenetres():
        if nom.lower() == titre:
            return hwnd
    return next((hwnd for nom, hwnd in lister_fenetres() if titre in nom.lower()), None)


# ------------------------------------------------------------------
#  Clic dans une fenêtre (relance du direct d'un lecteur)
# ------------------------------------------------------------------
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, MK_LBUTTON = 0x200, 0x201, 0x202, 0x203, 0x1


class DroitsInsuffisants(OSError):
    """La fenêtre appartient à un programme lancé en administrateur : Windows refuse qu'un
    programme ordinaire y clique (isolation des privilèges, UIPI)."""


def _client(hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    coin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(coin))
    return coin.x, coin.y, r.right, r.bottom


def cliquer(titre, point, double=True):
    """Clique au point (fx, fy), en fractions de la zone cliente, sans bouger la souris ni
    passer la fenêtre devant : le clic est posté à la fenêtre (ou à sa sous-fenêtre sous le
    point). DroitsInsuffisants si Windows le refuse ; OSError si la fenêtre a disparu."""
    hwnd = trouver_fenetre(titre)
    if not hwnd:
        raise OSError(t("fenêtre introuvable : {titre}", titre=titre))
    x0, y0, l, h = _client(hwnd)
    ecran = wt.POINT(int(x0 + point[0] * l), int(y0 + point[1] * h))
    # sous-fenêtre la plus profonde sous le point : c'est elle qui reçoit les clics
    cible = hwnd
    for _niveau in range(12):
        local = wt.POINT(ecran.x, ecran.y)
        user32.ScreenToClient(cible, ctypes.byref(local))
        enfant = user32.ChildWindowFromPointEx(cible, local, 0x0001 | 0x0002)   # visibles et actives
        if not enfant or enfant == cible:
            break
        cible = enfant
    local = wt.POINT(ecran.x, ecran.y)
    user32.ScreenToClient(cible, ctypes.byref(local))
    coords = (local.y & 0xFFFF) << 16 | (local.x & 0xFFFF)
    # un passage de la souris d'abord : les boutons ne réagissent qu'au clic « survolé »
    messages = [WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP] + ([WM_LBUTTONDBLCLK, WM_LBUTTONUP] if double else [])
    for m in messages:
        # user32 est chargé avec use_last_error : l'erreur se lit dans la copie de ctypes,
        # kernel32.GetLastError() rendrait une ancienne valeur
        ctypes.set_last_error(0)
        if not user32.PostMessageW(cible, m, MK_LBUTTON if m in (WM_LBUTTONDOWN, WM_LBUTTONDBLCLK) else 0, coords):
            if ctypes.get_last_error() == 5:
                raise DroitsInsuffisants(t("clic refusé par Windows : le programme tourne en administrateur"))
            raise OSError(t("clic impossible"))
        time.sleep(0.05)
    return True


def apprendre_point(titre, duree=20.0, immobile=2.5, arret=None):
    """Attend que la souris reste immobile `immobile` secondes au-dessus de la fenêtre, et
    renvoie ce point en fractions de sa zone cliente ; None après `duree` secondes."""
    hwnd = trouver_fenetre(titre)
    if not hwnd:
        return None
    fin = time.time() + duree
    dernier, depuis = None, time.time()
    while time.time() < fin and not (arret and arret.is_set()):
        p = wt.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        x0, y0, l, h = _client(hwnd)
        dedans = x0 <= p.x < x0 + l and y0 <= p.y < y0 + h
        if dernier is None or abs(p.x - dernier[0]) > 3 or abs(p.y - dernier[1]) > 3 or not dedans:
            dernier, depuis = (p.x, p.y), time.time()
        elif time.time() - depuis >= immobile:
            return round((p.x - x0) / l, 4), round((p.y - y0) / h, 4)
        time.sleep(0.05)
    return None


def _lire_bitmap(dc, bitmap, l, h):
    info = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=l, biHeight=-h, biPlanes=1, biBitCount=32)
    pixels = np.empty((h, l, 4), np.uint8)
    if not gdi32.GetDIBits(dc, bitmap, 0, h, pixels.ctypes.data, ctypes.byref(info), 0):
        return None
    return pixels[:, :, :3].copy()


GA_ROOT = 2


def fenetre_couverte(hwnd, x, y, l, h):
    """Vrai si une autre fenêtre cache une partie de la zone cliente (9 points testés).
    Ne sert que pour la copie d'écran : PrintWindow, lui, ne voit pas les fenêtres du dessus."""
    for fx in (0.1, 0.5, 0.9):
        for fy in (0.1, 0.5, 0.9):
            pt = wt.POINT(int(x + fx * l), int(y + fy * h))
            dessus = user32.WindowFromPoint(pt)
            if dessus and user32.GetAncestor(dessus, GA_ROOT) != hwnd:
                return True
    return False


def capturer_fenetre(hwnd):
    """Image BGR du contenu de la fenêtre, ou None si elle est fermée ou réduite.
    PrintWindow marche même si la fenêtre est cachée par une autre. Certains lecteurs vidéo
    rendent alors une image noire : on copie dans ce cas la zone de l'écran, ce qui demande
    que la fenêtre soit visible."""
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    l, h = r.right - r.left, r.bottom - r.top
    if l < 16 or h < 16:
        return None
    ecran = user32.GetDC(0)
    dc = gdi32.CreateCompatibleDC(ecran)
    bitmap = gdi32.CreateCompatibleBitmap(ecran, l, h)
    ancien = gdi32.SelectObject(dc, bitmap)
    try:
        image = None
        if user32.PrintWindow(hwnd, dc, 1 | PW_RENDERFULLCONTENT):    # 1 : zone cliente seulement
            image = _lire_bitmap(dc, bitmap, l, h)
        if image is None or image.mean() < 2:
            coin = wt.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(coin))
            if fenetre_couverte(hwnd, coin.x, coin.y, l, h):
                return None       # sinon on enregistrerait la fenêtre posée par-dessus
            gdi32.BitBlt(dc, 0, 0, l, h, ecran, coin.x, coin.y, SRCCOPY)
            image = _lire_bitmap(dc, bitmap, l, h)
        return image
    finally:
        gdi32.SelectObject(dc, ancien)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(0, ecran)


ECART_FOND = 14          # écart de couleur au fond uni, sur 255
PART_CONTENU = 0.03      # une ligne ou une colonne est utile si 3 % de ses pixels s'écartent du fond
SEUIL_MOUVEMENT = 4      # écart d'un pixel entre images, sur 255 : l'interface d'un lecteur ne bouge pas du tout
PART_MOUVEMENT = 0.10    # une ligne ou une colonne de la vidéo bouge sur 10 % de ses pixels au moins
LIGNE_UNIE = 0.08        # une ligne est un bord (cadre, fond) si moins de 8 % de ses pixels s'écartent de sa médiane
REDUCTION = 4            # le mouvement se mesure sur une image 4 fois plus petite
CALIBRAGE_S = 2.5        # durée d'observation au démarrage, pour trouver la vidéo dans la fenêtre
CALIBRAGE_MAX_S = 10.0   # si rien ne bouge encore, on attend jusqu'à 10 s
REVOIR_CADRE_S = 60.0    # le cadre est recalculé toutes les minutes
MARGE_CADRE = 8          # en pixels : en dessous, un nouveau cadre n'est pas pris en compte


def _valider_cadre(x0, y0, x1, y1, l, h):
    if (x1 - x0) < 0.15 * l or (y1 - y0) < 0.15 * h or ((x1 - x0) * (y1 - y0)) > 0.95 * l * h:
        return None                          # trop petit pour être la vidéo, ou rien à enlever
    return int(x0), int(y0), int(x1), int(y1)


def _ligne_unie(pixels):
    ecart = np.abs(pixels.astype(np.int16) - np.median(pixels, axis=0)).max(axis=-1)
    return (ecart > ECART_FOND).mean() < LIGNE_UNIE


def etendre_cadre(image, cadre):
    """Agrandit un cadre trouvé par le mouvement jusqu'aux bords unis qui entourent la vidéo :
    le ciel ou le gazon bougent peu, mais restent de l'image. On s'arrête à la première ligne
    unie (liseré, bande de fond) ou au bord de la fenêtre."""
    h, l = image.shape[:2]
    x0, y0, x1, y1 = cadre
    while y0 > 0 and not _ligne_unie(image[y0 - 1, x0:x1]):
        y0 -= 1
    while y1 < h and not _ligne_unie(image[y1, x0:x1]):
        y1 += 1
    while x0 > 0 and not _ligne_unie(image[y0:y1, x0 - 1]):
        x0 -= 1
    while x1 < l and not _ligne_unie(image[y0:y1, x1]):
        x1 += 1
    return x0, y0, x1, y1


def cadre_mouvement(petites, echelle=REDUCTION, image=None):
    """Cadre de la vidéo dans une fenêtre de lecteur : la partie qui change d'une image à
    l'autre. Boutons, listes et fonds restent fixes ; même une caméra devant un décor immobile
    bouge un peu (bruit de compression). petites : images en niveaux de gris réduites.
    Renvoie (x0, y0, x1, y1) en pixels de l'image d'origine, ou None."""
    if len(petites) < 4:
        return None
    pile = np.stack(petites).astype(np.int16)
    vivant = (pile.max(axis=0) - pile.min(axis=0)) > SEUIL_MOUVEMENT
    h, l = vivant.shape
    colonnes = np.flatnonzero(vivant.mean(axis=0) > PART_MOUVEMENT)
    lignes = np.flatnonzero(vivant.mean(axis=1) > PART_MOUVEMENT)
    if len(colonnes) == 0 or len(lignes) == 0:
        return None
    cadre = _valider_cadre(colonnes[0], lignes[0], colonnes[-1] + 1, lignes[-1] + 1, l, h)
    if cadre is None:
        return None
    cadre = tuple(min(int(v * echelle), borne) for v, borne in zip(cadre, (l * echelle, h * echelle) * 2))
    if image is not None:
        cadre = etendre_cadre(image, cadre)
        cadre = _valider_cadre(*cadre, image.shape[1], image.shape[0])
    return cadre


def cadre_utile(image):
    """Cadre sans les bandes unies autour (fond gris ou noir d'un lecteur), en jusqu'à trois
    passes : une bande noire autour d'un fond gris s'enlève en deux fois. None si rien."""
    x, y, x1, y1 = 0, 0, image.shape[1], image.shape[0]
    trouve = False
    for _passe in range(3):
        cadre = _cadre_une_fois(image[y:y1, x:x1])
        if cadre is None:
            break
        trouve = True
        x, y, x1, y1 = x + cadre[0], y + cadre[1], x + cadre[2], y + cadre[3]
    return (x, y, x1, y1) if trouve else None


def _cadre_une_fois(image):
    """La couleur du fond est la plus fréquente sur le bord de l'image."""
    h, l = image.shape[:2]
    bord = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]]).astype(np.int16)
    couleurs, nombres = np.unique(bord // 4, axis=0, return_counts=True)
    fond = couleurs[nombres.argmax()] * 4 + 2
    if nombres.max() < 0.3 * len(bord):
        return None                          # bord varié : pas de fond uni
    ecart = (np.abs(image.astype(np.int16) - fond).max(axis=2) > ECART_FOND)
    colonnes = np.flatnonzero(ecart.mean(axis=0) > PART_CONTENU)
    lignes = np.flatnonzero(ecart.mean(axis=1) > PART_CONTENU)
    if len(colonnes) == 0 or len(lignes) == 0:
        return None
    return _valider_cadre(colonnes[0], lignes[0], colonnes[-1] + 1, lignes[-1] + 1, l, h)


DWMWA_EXTENDED_FRAME_BOUNDS = 9


def zone_cliente(hwnd, image):
    """Windows Graphics Capture renvoie toute la fenêtre, barre de titre et bordures comprises
    (son cadre visible, DWMWA_EXTENDED_FRAME_BOUNDS). On ne garde que la zone cliente, comme
    la capture GDI."""
    cadre = wt.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                                  ctypes.byref(cadre), ctypes.sizeof(cadre)) != 0:
        return image
    client = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    coin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(coin))
    x0, y0 = coin.x - cadre.left, coin.y - cadre.top
    x1, y1 = x0 + client.right, y0 + client.bottom
    h, l = image.shape[:2]
    if client.right < 16 or client.bottom < 16 or x0 < 0 or y0 < 0 or x1 > l + 2 or y1 > h + 2:
        return image          # échelle d'affichage inattendue : on garde tout plutôt que de couper faux
    return image[y0:min(y1, h), x0:min(x1, l)]


class _CaptureGraphique:
    """Windows Graphics Capture (celle d'OBS et de la Barre de jeu) : voit le contenu d'une
    fenêtre même cachée par d'autres, lecteurs vidéo accélérés compris (EZVIZ Studio).
    La dernière image reçue est gardée ; Windows n'en envoie que quand la fenêtre change."""

    def __init__(self, hwnd):
        import threading
        from windows_capture import WindowsCapture      # absent : repli sur la capture GDI
        self.verrou = threading.Lock()
        self.image = None
        self.recue = 0.0
        self.fermee = False
        self.controle = None
        derniere_erreur = None
        for bordure in (False, None):    # sans bordure jaune si Windows le permet (Windows 11)
            try:
                capture = WindowsCapture(cursor_capture=False, draw_border=bordure,
                                         minimum_update_interval=int(1000 / IMAGES_PAR_SECONDE_FENETRE),
                                         window_hwnd=int(hwnd))

                @capture.event
                def on_frame_arrived(frame, _controle):
                    image = np.ascontiguousarray(frame.frame_buffer[:, :, :3])
                    with self.verrou:
                        self.image, self.recue = image, time.time()

                @capture.event
                def on_closed():
                    self.fermee = True

                self.controle = capture.start_free_threaded()
                return
            except Exception as erreur:            # noqa: BLE001 - bordure refusée, API absente...
                derniere_erreur = erreur
        raise RuntimeError(derniere_erreur)

    def lire(self):
        with self.verrou:
            # fenêtre immobile : Windows n'envoie plus d'image, la dernière reste valable
            if self.fermee or self.image is None:
                return None
            return self.image.copy()

    def arreter(self):
        if self.controle is not None:
            try:
                self.controle.stop()
            except Exception:                      # noqa: BLE001 - déjà arrêtée
                pass
            self.controle = None


class CaptureFenetre:
    """Une fenêtre ouverte, vue comme une caméra.
    - Windows Graphics Capture si le paquet windows-capture est installé : la fenêtre peut
      être cachée par d'autres. Sinon capture GDI : certains lecteurs doivent rester visibles.
    - recadrer : ne garde que la vidéo. Au démarrage, la fenêtre est observée CALIBRAGE_S
      secondes : la zone qui bouge est la vidéo (cadre_mouvement) ; à défaut, on enlève les
      bandes unies (cadre_utile). Le cadre est revu toutes les minutes.
    - Fenêtre réduite ou fermée : read() échoue ; la surveillance réessaie seule. Si elle change
      de taille, l'image est remise à la taille d'origine."""

    def __init__(self, titre, recadrer=True, graphique=True):
        self.titre = titre
        self.hwnd = trouver_fenetre(titre)
        self.suivante = 0.0
        self.taille = None
        self.recadrer = recadrer
        self.cadre = None
        self.cadre_revu = 0.0
        self.candidat = None
        self.petites = []
        self.taille_brute = None
        self.graphique = None
        if self.hwnd and graphique:
            try:
                self.graphique = _CaptureGraphique(self.hwnd)
            except Exception:                      # noqa: BLE001 - paquet absent ou Windows trop ancien
                self.graphique = None

    @property
    def mode(self):
        return "graphique" if self.graphique else "gdi"

    def _brute(self):
        if not self.hwnd or not user32.IsWindow(self.hwnd) or user32.IsIconic(self.hwnd):
            return None
        if self.graphique:
            image = self.graphique.lire()
            return None if image is None else zone_cliente(self.hwnd, image)
        return capturer_fenetre(self.hwnd)

    def _noter(self, image):
        petite = cv2.cvtColor(cv2.resize(image, (image.shape[1] // REDUCTION, image.shape[0] // REDUCTION),
                                         interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        if self.petites and self.petites[-1].shape != petite.shape:
            self.petites.clear()
        self.petites.append(petite)
        del self.petites[:-30]

    def calibrer(self):
        """Observe la fenêtre pour trouver la vidéo. Appelé à l'ouverture de la source."""
        if not self.recadrer:
            return
        debut = time.time()
        image, cadre = None, None
        # au moins CALIBRAGE_S ; si rien ne bouge encore (direct qui démarre), jusqu'à CALIBRAGE_MAX_S
        while time.time() - debut < CALIBRAGE_MAX_S:
            brute = self._brute()
            if brute is not None:
                image = brute
                self._noter(brute)
            if time.time() - debut >= CALIBRAGE_S and image is not None:
                cadre = cadre_mouvement(self.petites, image=image)
                if cadre is not None:
                    break
            time.sleep(1 / IMAGES_PAR_SECONDE_FENETRE)
        if image is not None:
            self.cadre = cadre or cadre_utile(image) or (0, 0, image.shape[1], image.shape[0])
            self.taille_brute = image.shape[:2]
            self.cadre_revu = time.time()

    def _revoir_cadre(self, image):
        """Un nouveau cadre n'est adopté que s'il est trouvé deux fois de suite : une scène
        immobile un moment ne fait pas sauter le recadrage."""
        if self.cadre is None or image.shape[:2] != self.taille_brute:   # 1re image ou fenêtre redimensionnée
            self.petites.clear()
            self._noter(image)
            self.cadre = cadre_utile(image) or (0, 0, image.shape[1], image.shape[0])
            self.taille_brute = image.shape[:2]
            self.cadre_revu = time.time() - REVOIR_CADRE_S + CALIBRAGE_S    # vrai cadre dans 2,5 s
            self.candidat = None
            self.taille = None
            return
        self._noter(image)
        if time.time() - self.cadre_revu < REVOIR_CADRE_S:
            return
        self.cadre_revu = time.time()
        nouveau = cadre_mouvement(self.petites, image=image)
        if nouveau is None or max(abs(a - b) for a, b in zip(nouveau, self.cadre)) <= MARGE_CADRE:
            self.candidat = None
        elif self.candidat is not None and max(abs(a - b) for a, b in zip(nouveau, self.candidat)) <= MARGE_CADRE:
            self.cadre, self.candidat = nouveau, None
            self.taille = None       # nouvelle taille : la surveillance la met au format, sans déformer
        else:
            self.candidat = nouveau
            self.cadre_revu = time.time() - REVOIR_CADRE_S + CALIBRAGE_S    # confirmation dans 2,5 s

    def isOpened(self):
        return self.hwnd is not None

    def get(self, _propriete):
        return IMAGES_PAR_SECONDE_FENETRE

    def read(self):
        attente = self.suivante - time.time()
        if attente > 0:
            time.sleep(attente)
        self.suivante = time.time() + 1 / IMAGES_PAR_SECONDE_FENETRE
        image = self._brute()
        if image is None:
            return False, None
        if self.recadrer:
            self._revoir_cadre(image)
            x0, y0, x1, y1 = self.cadre
            image = image[y0:y1, x0:x1]
        if self.taille is None:
            self.taille = (image.shape[1], image.shape[0])
        elif (image.shape[1], image.shape[0]) != self.taille:
            image = cv2.resize(image, self.taille)
        return True, image

    def release(self):
        if self.graphique:
            self.graphique.arreter()
            self.graphique = None
        self.hwnd = None


# ------------------------------------------------------------------
#  Ouverture
# ------------------------------------------------------------------
def ouvrir_source(source, largeur=1920, hauteur=1080):
    """Ouvre la source et lit une première image : (capture, image) ou (None, None).
    Webcam : 1080p en MJPG (par défaut OpenCV reste en 640x480, trop peu pour les écailles) ;
    Media Foundation d'abord, puis DirectShow (certaines webcams ne renvoient rien sous MSMF)."""
    if est_fenetre(source):
        camera = CaptureFenetre(source[len(PREFIXE_FENETRE):])
        if not camera.isOpened():
            return None, None
        camera.calibrer()
    elif isinstance(source, str):
        adresse = resoudre_flux(source) if est_flux(source) else source
        camera = cv2.VideoCapture(adresse, cv2.CAP_FFMPEG) if est_flux(source) else cv2.VideoCapture(adresse)
    else:
        for api in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
            camera = cv2.VideoCapture(source, api)
            if camera.isOpened():
                camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, largeur)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, hauteur)
                for _i in range(8):     # certaines webcams renvoient quelques images vides au démarrage
                    ok, image = camera.read()
                    if ok:
                        return camera, image
            camera.release()
        return None, None
    ok, image = camera.read()
    if ok:
        return camera, image
    camera.release()
    return None, None
