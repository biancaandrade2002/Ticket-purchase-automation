@echo off
REM ============================================================
REM  INICIAR BOT DE INGRESSOS - COM WATCHDOG
REM  O watchdog reinicia o bot automaticamente se ele parar
REM  (standby, crash, erro de rede, Chrome morto, etc.)
REM ============================================================
REM  Dois cliques neste arquivo para iniciar.
REM  Para PARAR: feche esta janela ou aperte Ctrl+C.
REM  Mantenha o notebook NA TOMADA.
REM ============================================================

title Bot de Ingressos ^| WATCHDOG ATIVO

REM Vai para a pasta onde este arquivo esta (a pasta do projeto).
cd /d "%~dp0.."

REM Modo de monitoramento: somente Ticketmaster.
set MONITOR_ONLY=ticketmaster

echo ============================================================
echo   BOT DE INGRESSOS - WATCHDOG ATIVO
echo   O bot sera reiniciado automaticamente apos standby.
echo   Para parar: Ctrl+C ou feche esta janela.
echo ============================================================
echo.

REM Tenta usar o Python do venv do MCP primeiro; cai para o Python do sistema.
if exist "src\mcps\buyticket\.venv\Scripts\python.exe" (
    "src\mcps\buyticket\.venv\Scripts\python.exe" watchdog.py
) else (
    py watchdog.py
)

REM Se o watchdog encerrar (Ctrl+C), a janela fica aberta.
echo.
echo ============================================================
echo   WATCHDOG ENCERRADO. Feche quando quiser.
echo ============================================================
pause
