@echo off
setlocal
cd /d "%~dp0"
echo === Python ===
py --version
echo.
echo === Pip ===
py -m pip --version
echo.
echo === Required packages ===
py -m pip show fastapi uvicorn pydantic google-genai python-dotenv
echo.
echo === Server import test ===
py -c "import server.main; print('server.main import: OK')"
echo.
pause
endlocal
