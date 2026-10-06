@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo ERROR: Python was not found.
        pause
        exit /b 1
    )
)

echo.
echo ==========================================
echo   NCERT Solutions Maker - Local Gateway
echo ==========================================
echo.
%PY% --version
echo.
%PY% "%~dp0server\main.py"

echo.
echo Gateway stopped.
pause
endlocal
