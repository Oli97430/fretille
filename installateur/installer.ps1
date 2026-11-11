# Frétille — préparation de l'environnement, appelée par Fretille-Setup.exe (ou à la main).
#   1. trouve Python 3.11, ou l'installe pour l'utilisateur (installateur officiel de python.org, signature vérifiée)
#   2. crée l'environnement privé  <dossier>\venv
#   3. installe PyTorch (version carte NVIDIA si elle est là, sinon processeur) puis les bibliothèques
# Relancer ce script répare ou met à jour une installation. Journal : <dossier>\installation.log
param(
    [Parameter(Mandatory = $true)][string]$Dossier
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"        # sinon Invoke-WebRequest est dix fois plus lent
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$VersionPython = "3.11.9"
$UrlPython = "https://www.python.org/ftp/python/$VersionPython/python-$VersionPython-amd64.exe"
$Journal = Join-Path $Dossier "installation.log"
$Francais = (Get-UICulture).TwoLetterISOLanguageName -eq "fr"

function Dire([string]$fr, [string]$en) {
    $texte = if ($Francais) { $fr } else { $en }
    Write-Host ""
    Write-Host "==> $texte" -ForegroundColor Cyan
    Add-Content -Path $Journal -Value "$(Get-Date -Format s)  $texte" -Encoding UTF8
}

function Echec([string]$fr, [string]$en) {
    $texte = if ($Francais) { $fr } else { $en }
    Write-Host ""
    Write-Host "XX  $texte" -ForegroundColor Red
    Add-Content -Path $Journal -Value "$(Get-Date -Format s)  ECHEC  $texte" -Encoding UTF8
    exit 1
}

function Bon-Python([string]$chemin) {
    # Python 3.11, 64 bits, avec Tk (l'interface) et venv
    if (-not $chemin -or -not (Test-Path $chemin)) { return $false }
    try {
        $sortie = & $chemin -c "import sys, struct, tkinter, venv; print(sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8)" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$sortie".Trim() -eq "3 11 64")
    } catch { return $false }
}

function Trouver-Python {
    $candidats = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe")
    )
    try {
        $lanceur = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $lanceur) { $candidats += "$lanceur".Trim() }
    } catch { }
    foreach ($c in $candidats) { if (Bon-Python $c) { return $c } }
    return $null
}

function Installer-Python {
    Dire "Python 3.11 est absent : telechargement depuis python.org (25 Mo)..." "Python 3.11 not found: downloading from python.org (25 MB)..."
    $fichier = Join-Path $env:TEMP "python-$VersionPython-amd64.exe"
    Invoke-WebRequest -Uri $UrlPython -OutFile $fichier -UseBasicParsing
    $signature = Get-AuthenticodeSignature $fichier
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        Remove-Item $fichier -Force -ErrorAction SilentlyContinue
        Echec "L'installateur de Python n'est pas signe par la Python Software Foundation : arret." "The Python installer is not signed by the Python Software Foundation: stopping."
    }
    Dire "Installation de Python 3.11 (pour vous seulement, sans droits administrateur)..." "Installing Python 3.11 (for you only, no administrator rights)..."
    $p = Start-Process -FilePath $fichier -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=0", "Include_test=0", "Include_doc=0", "Shortcuts=0")
    Remove-Item $fichier -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { Echec "L'installation de Python a echoue (code $($p.ExitCode))." "Python installation failed (code $($p.ExitCode))." }
}

function Lancer([string]$exe, [string[]]$arguments, [string]$fr, [string]$en) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Echec $fr $en }
}

try {
    New-Item -ItemType Directory -Force -Path $Dossier | Out-Null
    Set-Content -Path $Journal -Value "$(Get-Date -Format s)  Installation de Fretille dans $Dossier" -Encoding UTF8

    $lecteur = (Get-Item $Dossier).PSDrive
    if ($lecteur.Free -lt 9GB) {
        Echec "Il faut 9 Go libres sur le disque $($lecteur.Name): (il en reste $([math]::Round($lecteur.Free / 1GB, 1)))." "9 GB of free space are needed on drive $($lecteur.Name): ($([math]::Round($lecteur.Free / 1GB, 1)) left)."
    }

    Dire "Etape 1 sur 4 : Python" "Step 1 of 4: Python"
    $python = Trouver-Python
    if (-not $python) {
        Installer-Python
        $python = Trouver-Python
        if (-not $python) { Echec "Python 3.11 reste introuvable apres son installation." "Python 3.11 still not found after installing it." }
    }
    Write-Host "    $python"

    Dire "Etape 2 sur 4 : environnement prive de Fretille" "Step 2 of 4: Fretille's private environment"
    $venv = Join-Path $Dossier "venv"
    $py = Join-Path $venv "Scripts\python.exe"
    if (-not (Bon-Python $py)) {
        if (Test-Path $venv) { Remove-Item $venv -Recurse -Force }
        Lancer $python @("-m", "venv", $venv) "Environnement impossible a creer." "Could not create the environment."
    }
    Lancer $py @("-m", "pip", "install", "--upgrade", "pip", "--quiet", "--disable-pip-version-check") "pip impossible a mettre a jour (Internet ?)." "Could not update pip (Internet?)."

    $nvidia = $false
    try { & nvidia-smi *> $null; $nvidia = ($LASTEXITCODE -eq 0) } catch { }
    if ($nvidia) {
        Dire "Etape 3 sur 4 : PyTorch pour carte NVIDIA (environ 2,5 Go, soyez patient)" "Step 3 of 4: PyTorch for NVIDIA cards (about 2.5 GB, please wait)"
        $index = "https://download.pytorch.org/whl/cu126"
    } else {
        Dire "Etape 3 sur 4 : PyTorch pour processeur (environ 250 Mo)" "Step 3 of 4: PyTorch for CPU (about 250 MB)"
        $index = "https://download.pytorch.org/whl/cpu"
    }
    # même version de torch que requirements.txt : sinon l'étape 4 la remplacerait par la version processeur
    $torch = Get-Content (Join-Path $Dossier "requirements.txt") | Where-Object { $_ -match "^torch==" } | Select-Object -First 1
    if (-not $torch) { $torch = "torch" }
    Lancer $py @("-m", "pip", "install", "$torch".Trim(), "torchvision", "--index-url", $index, "--disable-pip-version-check") "PyTorch impossible a installer (Internet ? place disque ?)." "Could not install PyTorch (Internet? disk space?)."

    Dire "Etape 4 sur 4 : bibliotheques de Fretille" "Step 4 of 4: Fretille's libraries"
    Lancer $py @("-m", "pip", "install", "-r", (Join-Path $Dossier "requirements.txt"), "--disable-pip-version-check") "Bibliotheques impossibles a installer." "Could not install the libraries."

    # verification : tout ce dont l'application a besoin se charge
    Lancer $py @("-c", "import cv2, numpy, PIL, torch, transformers, timm, open_clip, segno, tkinter") "Une bibliotheque ne se charge pas." "A library fails to load."

    Dire "Fretille est pret. Les modeles de vision (1,5 Go) se telechargent au premier lancement." "Fretille is ready. The vision models (1.5 GB) download on first launch."
    exit 0
} catch {
    Echec "Erreur inattendue : $($_.Exception.Message)" "Unexpected error: $($_.Exception.Message)"
}
