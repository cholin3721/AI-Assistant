@echo off
chcp 65001 >nul
title 인하 AI 비서
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo.
  echo  [!] Python이 설치되어 있지 않아요.
  echo      잠시 후 열리는 페이지에서 Python 3.10 이상을 설치해주세요.
  echo      설치 첫 화면에서 "Add python.exe to PATH" 체크를 꼭 켜주세요!
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  처음 실행이라 필요한 프로그램을 설치하고 있어요. 1~2분 걸려요...
  %PY% -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo  [!] 설치 중 문제가 생겼어요. 인터넷 연결을 확인하고 다시 실행해주세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main %*
pause
