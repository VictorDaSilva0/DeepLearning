@echo off
rem Lance DeepLearning : cree l'environnement Python au premier lancement,
rem installe les dependances si besoin, puis ouvre l'interface dans le navigateur.
setlocal
cd /d "%~dp0"
title DeepLearning

if not exist ".venv\Scripts\python.exe" (
    echo Premier lancement : creation de l'environnement Python...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

fc /b requirements.txt ".venv\requirements.stamp" >nul 2>&1
if errorlevel 1 (
    echo Installation des dependances...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo.
        echo L'installation des dependances a echoue.
        pause
        exit /b 1
    )
    copy /y requirements.txt ".venv\requirements.stamp" >nul
)

".venv\Scripts\python.exe" run.py %*
pause
