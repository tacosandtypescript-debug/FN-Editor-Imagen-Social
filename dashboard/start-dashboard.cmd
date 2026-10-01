@echo off
rem Arranca el dashboard de EditImg accesible desde la red local y Tailscale.
rem Pensado para lanzarse desde una tarea programada o con doble clic.
setlocal
set "RAIZ=%~dp0.."
cd /d "%RAIZ%"

set "VAR=%~dp0var"
if not exist "%VAR%" mkdir "%VAR%"
set "LOG=%VAR%\dashboard.log"

rem Si el puerto ya está ocupado, no se arranca otro: se avisa y se sale.
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:":8765 " >nul 2>&1
if not errorlevel 1 (
  echo [%date% %time%] ya hay algo escuchando en el puerto 8765; no se arranca otro >> "%LOG%"
  exit /b 0
)

echo. >> "%LOG%"
echo [%date% %time%] arrancando EditImg Dashboard >> "%LOG%"
python -m dashboard --lan --port 8765 >> "%LOG%" 2>&1
echo [%date% %time%] el dashboard termino con codigo %errorlevel% >> "%LOG%"
exit /b %errorlevel%
