@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "HERE=%~dp0"
(
  echo Set sh = CreateObject^("WScript.Shell"^)
  echo sh.CurrentDirectory = "%HERE%"
  echo sh.Run """%HERE%run.bat"" --silent", 7, False
) > "%STARTUP%\InhaAIAssistant.vbs"
if exist "%STARTUP%\InhaAIAssistant.vbs" (
  echo.
  echo  등록 완료! 이제 윈도우를 켜면 AI 비서가 작업표시줄에 최소화된 채로 자동 실행돼요.
  echo  화면은 브라우저에서 http://127.0.0.1:8765 로 열면 돼요.
) else (
  echo  [!] 등록하지 못했어요.
)
echo.
pause
