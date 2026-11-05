"""
Watchdog — reinicia o bot automaticamente após standby, crash ou erro.

Como funciona:
  1. Inicia bot.py como subprocesso.
  2. Monitora se o processo ainda está vivo (polling a cada 5 s).
  3. Se o processo terminar por qualquer motivo (standby, crash, exceção
     não tratada), aguarda RESTART_DELAY segundos e reinicia.
  4. Limpa bot.lock antes de cada restart para evitar trava de instância.
  5. Registra tudo em bot.log, igual ao próprio bot.

Uso:
  py watchdog.py                  ← abre navegador visível
  py watchdog.py --headless       ← sem interface gráfica
  Ou use o arquivo INICIAR_BOT.bat (clique duplo).
"""

import os
import sys
import time
import signal
import datetime
import subprocess

# ── Configurações ──────────────────────────────────────────────────────────────
RESTART_DELAY   = 20          # segundos entre reinícios
POLL_INTERVAL   = 5           # segundos entre checagens de processo vivo
MAX_RESTARTS    = 999         # reinícios máximos (999 = praticamente ilimitado)
LOG_FILE        = 'bot.log'

# Localiza os arquivos na mesma pasta deste script.
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT  = os.path.join(BASE_DIR, 'bot.py')
LOCK_FILE   = os.path.join(BASE_DIR, 'bot.lock')

# Usa o mesmo intérprete Python que está rodando o watchdog.
PYTHON = sys.executable


# ── Utilidades ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    linha = f'[{ts}] [WATCHDOG] {msg}'
    print(linha, flush=True)
    try:
        with open(os.path.join(BASE_DIR, LOG_FILE), 'a', encoding='utf-8') as f:
            f.write(linha + '\n')
    except Exception:
        pass


def limpar_lock() -> None:
    """Remove o arquivo de lock órfão para que o bot possa iniciar."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            log('Lock orphão removido — bot pode reiniciar limpo.')
    except Exception as e:
        log(f'Aviso: nao foi possivel remover lock ({e}). Pode travar na proxima tentativa.')


def matar_chromes_orfaos() -> None:
    """
    Encerra processos chromedriver/chrome que ficaram abertos após um crash.
    Usa a mesma lógica conservadora do bot.py (marcadores no cmdline).
    """
    try:
        import subprocess as _sp
        marcadores = ['--remote-debugging-port', '--test-type', 'chromedriver']
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter "
            "\"name='chrome.exe' OR name='chromedriver.exe'\" | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
        )
        saida = _sp.run(
            ['powershell', '-NoProfile', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=20,
        ).stdout
        mortos = 0
        for linha in saida.splitlines():
            if '|' not in linha:
                continue
            pid_str, _, cmdline = linha.partition('|')
            if any(m in (cmdline or '').lower() for m in marcadores):
                try:
                    os.kill(int(pid_str.strip()), signal.SIGTERM)
                    mortos += 1
                except Exception:
                    pass
        if mortos:
            log(f'{mortos} processo(s) Chrome orfao(s) encerrado(s).')
    except Exception:
        pass


# ── Loop principal ─────────────────────────────────────────────────────────────

def main() -> None:
    headless = '--headless' in sys.argv

    log('=' * 60)
    log('Watchdog iniciado.')
    log(f'Script alvo: {BOT_SCRIPT}')
    log(f'Headless: {headless}')
    log(f'Restart delay: {RESTART_DELAY}s  |  Poll: {POLL_INTERVAL}s')
    log('=' * 60)

    tentativas = 0
    proc = None

    # Captura Ctrl+C para encerrar limpo.
    encerrar = [False]

    def _sair(signum, frame):
        log('Ctrl+C recebido. Encerrando watchdog...')
        encerrar[0] = True
        if proc and proc.poll() is None:
            proc.terminate()

    signal.signal(signal.SIGINT, _sair)
    signal.signal(signal.SIGTERM, _sair)

    while not encerrar[0] and tentativas < MAX_RESTARTS:
        tentativas += 1
        log(f'--- Tentativa #{tentativas} ---')

        # Limpa estado anterior antes de cada início.
        limpar_lock()
        matar_chromes_orfaos()
        time.sleep(2)

        # Monta comando (repassa --headless se necessário).
        cmd = [PYTHON, BOT_SCRIPT]
        if headless:
            # bot.py lê HEADLESS do .env; aqui sobrescrevemos via variável de ambiente.
            env = os.environ.copy()
            env['HEADLESS'] = 'true'
        else:
            env = None

        try:
            proc = subprocess.Popen(cmd, cwd=BASE_DIR, env=env)
            log(f'Bot iniciado (PID {proc.pid}).')

            # Polling: aguarda o processo ou detecta encerramento.
            while not encerrar[0]:
                ret = proc.poll()
                if ret is not None:
                    log(f'Bot encerrou com codigo {ret}.')
                    break
                time.sleep(POLL_INTERVAL)

        except Exception as e:
            log(f'Erro ao iniciar bot: {e}')

        if encerrar[0]:
            break

        log(f'Reiniciando em {RESTART_DELAY}s... (tentativa {tentativas}/{MAX_RESTARTS})')
        for _ in range(RESTART_DELAY):
            if encerrar[0]:
                break
            time.sleep(1)

    log('Watchdog encerrado.')


if __name__ == '__main__':
    main()
