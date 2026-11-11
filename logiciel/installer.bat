@echo off
rem Prepare Fretille sur ce PC : Python 3.11, PyTorch (carte NVIDIA si presente), bibliotheques, modeles.
rem Environ 3 Go telecharges au premier lancement. A ne faire qu'une fois.
cd /d "%~dp0"
py -3.11 -m venv venv || goto erreur
nvidia-smi >nul 2>&1
if %errorlevel%==0 (
    venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 || goto erreur
) else (
    venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto erreur
)
venv\Scripts\python -m pip install -r requirements.txt || goto erreur
echo Installation terminee : lancez Fretille.bat
pause
exit /b 0
:erreur
echo Installation impossible : Python 3.11 est-il installe ?
pause
exit /b 1
