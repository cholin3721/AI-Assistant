@echo off
chcp 65001 >nul
title 인하 AI 비서 실행 파일 만들기
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
  echo  먼저 run.bat 으로 한 번 실행해 프로그램을 설치해주세요.
  pause
  exit /b 1
)

echo  PyInstaller를 준비하고 실행 파일을 만들고 있어요. 몇 분 걸려요...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check pyinstaller
if errorlevel 1 (
  echo  [!] PyInstaller 설치에 실패했어요. 인터넷 연결을 확인해주세요.
  pause
  exit /b 1
)

".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --name InhaAIAssistant ^
  --add-data "app\static;app/static" ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import google.genai ^
  --collect-submodules discord ^
  app\main.py
if errorlevel 1 (
  echo  [!] 만들기에 실패했어요.
  pause
  exit /b 1
)

echo.
echo  완료: dist\InhaAIAssistant.exe
echo  이 파일을 아무 폴더에 복사해서 실행하면 됩니다. data 폴더는 exe 옆에 생깁니다.
echo  Python을 설치하지 않은 PC에서도 동작해요.
echo.
pause
