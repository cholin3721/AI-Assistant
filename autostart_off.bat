@echo off
chcp 65001 >nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\InhaAIAssistant.vbs" (
  del "%STARTUP%\InhaAIAssistant.vbs"
  echo  자동 실행을 껐어요.
) else (
  echo  자동 실행이 등록돼 있지 않아요.
)
echo.
pause
