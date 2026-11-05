@echo off
REM ============================================================
REM  TESTE SEGURO DO BOT - NAO COMPRA NADA (modo simulacao)
REM ============================================================
REM  Use este para verificar que o bot sobe e monitora sem
REM  risco de compra. Ele faz todo o fluxo MENOS clicar em
REM  comprar (DRY_RUN=true).
REM  Para PARAR: Ctrl+C ou feche a janela.
REM ============================================================

title Bot de Ingressos - TESTE SEGURO (nao compra)

cd /d "%~dp0.."

REM Modo seguro: simula, NAO compra.
set COMPRA_REAL=false
set DRY_RUN=true
set HEADLESS=true

echo ============================================================
echo   Iniciando bot em MODO SEGURO (nao compra nada)...
echo   Para parar: Ctrl+C ou feche a janela.
echo ============================================================
echo.

"src\mcps\buyticket\.venv\Scripts\python.exe" bot.py

echo.
echo ============================================================
echo   O BOT PAROU. Feche quando quiser.
echo ============================================================
pause
