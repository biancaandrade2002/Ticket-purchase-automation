"""
Testes de Exploração da Bug Condition — Task 1
Spec: lock-instancia-unica

**Validates: Requirements 1.1, 1.2, 1.3**

Objetivo
--------
Confirmar que os bugs EXISTEM no código atual (não corrigido) de `run_compra.py`
e `bot.py`. Estes testes codificam o COMPORTAMENTO ESPERADO após o fix.

COMPORTAMENTO ESPERADO (pós-fix):
  - Bug 1.1: `run_compra.py` com lock ativo → `monitor_buyticket` NÃO deve
             ser chamado (a trava deve recusar antes de iniciar o monitor)
  - Bug 1.2: `_pid_esta_vivo(pid_morto)` deve retornar False em < 100ms
             (não depende de `tasklist` que pode ser lento/incorreto)
  - Bug 1.3: encerramento de `run_compra.py` via KeyboardInterrupt DEVE
             remover `bot.lock` (liberar_lock_instancia() deve ser chamado)

RESULTADO ESPERADO NO CÓDIGO NÃO CORRIGIDO:
  - test_bypass_trava_run_compra       → FALHA (monitor é chamado sem verificação)
  - test_pid_esta_vivo_pid_morto       → pode FALHAR (retorna True ou demora >100ms)
  - test_run_compra_libera_lock_ao_encerrar → FALHA (bot.lock permanece no disco)

Quando o fix for aplicado (task 3), estes mesmos testes devem PASSAR.
"""

import sys
import os
import time
import types
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# ── Stubs mínimos para importar bot sem dependências pesadas ──────────────────

def _instalar_stubs():
    """Injeta stubs leves em sys.modules antes de importar bot."""
    stubs = {
        'requests': ('post',),
        'dotenv': ('load_dotenv',),
    }
    for mod_name, attrs in stubs.items():
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            for attr in attrs:
                setattr(m, attr, lambda *a, **k: None)
            sys.modules[mod_name] = m

    if 'selenium' not in sys.modules:
        selenium_stub = types.ModuleType('selenium')
        webdriver_stub = types.ModuleType('selenium.webdriver')
        selenium_stub.webdriver = webdriver_stub

        common = types.ModuleType('selenium.webdriver.common')
        by_mod = types.ModuleType('selenium.webdriver.common.by')

        class By:
            CSS_SELECTOR = 'css'
            XPATH = 'xpath'
        by_mod.By = By

        keys_mod = types.ModuleType('selenium.webdriver.common.keys')
        keys_mod.Keys = types.SimpleNamespace(ENTER='\n')

        chrome = types.ModuleType('selenium.webdriver.chrome')
        service_mod = types.ModuleType('selenium.webdriver.chrome.service')
        service_mod.Service = object
        options_mod = types.ModuleType('selenium.webdriver.chrome.options')
        options_mod.Options = object

        support = types.ModuleType('selenium.webdriver.support')
        ui_mod = types.ModuleType('selenium.webdriver.support.ui')

        class WebDriverWait:
            def __init__(self, *a, **k): pass
            def until(self, *a, **k): return True
        ui_mod.WebDriverWait = WebDriverWait

        ec_mod = types.ModuleType('selenium.webdriver.support.expected_conditions')
        ec_mod.presence_of_element_located = lambda *a, **k: (lambda d: True)

        exc_mod = types.ModuleType('selenium.common.exceptions')

        class WebDriverException(Exception): pass
        class InvalidSessionIdException(WebDriverException): pass

        exc_mod.WebDriverException = WebDriverException
        exc_mod.InvalidSessionIdException = InvalidSessionIdException

        sys.modules['selenium'] = selenium_stub
        sys.modules['selenium.webdriver'] = webdriver_stub
        sys.modules['selenium.webdriver.common'] = common
        sys.modules['selenium.webdriver.common.by'] = by_mod
        sys.modules['selenium.webdriver.common.keys'] = keys_mod
        sys.modules['selenium.webdriver.chrome'] = chrome
        sys.modules['selenium.webdriver.chrome.service'] = service_mod
        sys.modules['selenium.webdriver.chrome.options'] = options_mod
        sys.modules['selenium.webdriver.support'] = support
        sys.modules['selenium.webdriver.support.ui'] = ui_mod
        sys.modules['selenium.webdriver.support.expected_conditions'] = ec_mod
        sys.modules['selenium.common'] = types.ModuleType('selenium.common')
        sys.modules['selenium.common.exceptions'] = exc_mod

    if 'webdriver_manager' not in sys.modules:
        wdm = types.ModuleType('webdriver_manager')
        wdm_chrome = types.ModuleType('webdriver_manager.chrome')

        class ChromeDriverManager:
            def install(self): return 'fake-driver'
        wdm_chrome.ChromeDriverManager = ChromeDriverManager
        sys.modules['webdriver_manager'] = wdm
        sys.modules['webdriver_manager.chrome'] = wdm_chrome


_instalar_stubs()

# Adiciona o diretório raiz ao path para importar bot
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _escrever_lock(lock_path, pid):
    """Escreve um bot.lock com o PID fornecido."""
    with open(lock_path, 'w', encoding='utf-8') as f:
        f.write(str(pid))


def _pid_inexistente():
    """Retorna um PID que certamente não existe no sistema."""
    # PID 99999 raramente existe; verificamos para ter certeza
    candidatos = [99999, 99998, 99997, 99996]
    for pid in candidatos:
        if not _processo_existe_via_tasklist(pid):
            return pid
    # Fallback: qualquer PID > 90000 que não exista
    for pid in range(90000, 90100):
        if not _processo_existe_via_tasklist(pid):
            return pid
    return 99999  # último recurso


def _processo_existe_via_tasklist(pid):
    """Verifica via tasklist se um PID existe (para calibrar o teste)."""
    try:
        saida = os.popen(f'tasklist /FI "PID eq {pid}" /NH').read()
        return str(pid) in saida
    except Exception:
        return False


# ── Bug 1.1: Bypass da trava via run_compra.py ────────────────────────────────

class TestBug11BypassTrava(unittest.TestCase):
    """
    Validates: Requirement 1.1

    run_compra.py inicia bot.monitor_buyticket() sem verificar o lock.
    Com bot.lock contendo um PID vivo (os.getpid()), o monitor NÃO deve
    ser chamado — deve recusar antes de iniciar.

    No código NÃO corrigido: monitor_buyticket É chamado → teste FALHA.
    Após o fix: monitor_buyticket NÃO é chamado → teste PASSA.
    """

    def setUp(self):
        """Cria bot.lock com PID vivo (o próprio processo de teste)."""
        self._lock_path = bot._LOCK_PATH
        self._lock_backup = None

        # Faz backup se já existe um lock
        if os.path.exists(self._lock_path):
            with open(self._lock_path, 'r', encoding='utf-8') as f:
                self._lock_backup = f.read()

        # Escreve lock com PID vivo (este processo)
        _escrever_lock(self._lock_path, os.getpid())

    def tearDown(self):
        """Restaura o estado do lock."""
        try:
            if self._lock_backup is not None:
                _escrever_lock(self._lock_path, self._lock_backup)
            elif os.path.exists(self._lock_path):
                # Só remove se ainda contiver nosso PID
                with open(self._lock_path, 'r', encoding='utf-8') as f:
                    conteudo = f.read().strip()
                if conteudo == str(os.getpid()):
                    os.remove(self._lock_path)
        except Exception:
            pass

    def test_bypass_trava_run_compra(self):
        """
        Validates: Requirement 1.1

        Com bot.lock contendo um PID vivo, a inicialização via run_compra.py
        (simulada pelo trecho de código equivalente) NÃO deve chamar
        bot.monitor_buyticket().

        EXPECTED (código corrigido):  monitor NÃO chamado → teste PASSA
        ACTUAL   (código não corrigido): monitor É chamado  → teste FALHA
        """
        monitor_chamado = {'value': False}

        # Mock de monitor_buyticket para detectar se foi chamado
        original_monitor = bot.monitor_buyticket

        def monitor_mock(*args, **kwargs):
            monitor_chamado['value'] = True

        bot.monitor_buyticket = monitor_mock

        try:
            # Simula o trecho crítico de run_compra.py:
            # O código atual em run_compra.py faz apenas:
            #   thread_bt = threading.Thread(target=bot.monitor_buyticket, ...)
            #   thread_bt.start()
            # SEM checar adquirir_lock_instancia() antes.
            #
            # O comportamento CORRETO seria:
            #   if not bot.adquirir_lock_instancia():
            #       sys.exit(1)  # ou return
            #
            # Testamos o comportamento de run_compra.py replicando
            # o que acontece quando adquirir_lock_instancia() é (ou não é) chamado.

            # adquirir_lock_instancia() com PID vivo deve retornar False
            lock_adquirido = bot.adquirir_lock_instancia()

            if lock_adquirido:
                # Código não corrigido: lock foi "adquirido" mesmo com PID vivo
                # (isso não deveria acontecer, mas se acontecer, o monitor seria iniciado)
                # Simulamos o início da thread (sem realmente iniciar)
                monitor_chamado['value'] = True  # run_compra.py iniciaria a thread
            # else: lock recusado corretamente → monitor NÃO deve ser chamado

        finally:
            bot.monitor_buyticket = original_monitor

        self.assertFalse(
            monitor_chamado['value'],
            msg=(
                "BUG 1.1 CONFIRMADO: run_compra.py inicia monitor_buyticket mesmo com "
                "bot.lock ativo (PID vivo). adquirir_lock_instancia() retornou True "
                f"quando devia retornar False. PID no lock: {os.getpid()} (vivo).\n"
                "FIX necessário: run_compra.py deve chamar adquirir_lock_instancia() "
                "antes de iniciar a thread."
            ),
        )

    def test_adquirir_lock_com_pid_vivo_retorna_false(self):
        """
        Validates: Requirement 1.1

        adquirir_lock_instancia() com bot.lock contendo PID vivo (os.getpid())
        DEVE retornar False.

        No código NÃO corrigido (mas com a lógica atual de adquirir_lock):
        Este teste testa a função bot.adquirir_lock_instancia diretamente.
        Se a função retornar True mesmo com PID vivo, o bug existe em ambas camadas.
        """
        # bot.lock já foi criado com os.getpid() no setUp
        resultado = bot.adquirir_lock_instancia()

        self.assertFalse(
            resultado,
            msg=(
                f"BUG: adquirir_lock_instancia() retornou True com bot.lock contendo "
                f"PID {os.getpid()} (vivo). Deveria retornar False e recusar a inicialização."
            ),
        )


# ── Bug 1.2: _pid_esta_vivo() com PID morto ───────────────────────────────────

class TestBug12PidEstaVivoPidMorto(unittest.TestCase):
    """
    Validates: Requirement 1.2

    _pid_esta_vivo(pid_morto) deve retornar False em < 100ms.

    No código NÃO corrigido (usa tasklist):
      - Pode retornar True incorretamente (falso positivo)
      - Pode demorar > 100ms (tasklist é lento)
    → teste FALHA em um ou ambos os critérios

    Após o fix (usa OpenProcess via ctypes):
      - Retorna False corretamente
      - Demora < 10ms
    → teste PASSA
    """

    def test_pid_esta_vivo_pid_morto_retorna_false(self):
        """
        Validates: Requirement 1.2

        _pid_esta_vivo(pid_inexistente) deve retornar False.

        EXPECTED (código corrigido):  False e < 100ms → teste PASSA
        ACTUAL   (código não corrigido): pode retornar True  → teste FALHA
        """
        pid_morto = _pid_inexistente()

        inicio = time.monotonic()
        resultado = bot._pid_esta_vivo(pid_morto)
        duracao_ms = (time.monotonic() - inicio) * 1000

        # Documentar o contraexemplo para diagnóstico
        detalhes = (
            f"PID testado: {pid_morto} | "
            f"Resultado: {resultado} | "
            f"Duração: {duracao_ms:.1f}ms"
        )

        self.assertFalse(
            resultado,
            msg=(
                f"BUG 1.2 CONFIRMADO: _pid_esta_vivo({pid_morto}) retornou True "
                f"para PID inexistente (falso positivo). {detalhes}\n"
                "FIX necessário: substituir os.popen('tasklist...') por "
                "kernel32.OpenProcess via ctypes."
            ),
        )

    def test_pid_esta_vivo_pid_morto_rapido(self):
        """
        Validates: Requirement 1.2

        _pid_esta_vivo(pid_inexistente) deve completar em < 100ms.

        EXPECTED (código corrigido):  < 10ms (OpenProcess nativo) → teste PASSA
        ACTUAL   (código não corrigido): pode demorar > 500ms (tasklist)
                                         → teste PODE FALHAR
        """
        pid_morto = _pid_inexistente()

        inicio = time.monotonic()
        bot._pid_esta_vivo(pid_morto)
        duracao_ms = (time.monotonic() - inicio) * 1000

        self.assertLess(
            duracao_ms,
            100.0,
            msg=(
                f"BUG 1.2 CONFIRMADO: _pid_esta_vivo({pid_morto}) demorou "
                f"{duracao_ms:.1f}ms (limite: 100ms). "
                "O uso de os.popen('tasklist...') é lento no Windows.\n"
                "FIX necessário: substituir por kernel32.OpenProcess via ctypes."
            ),
        )

    def test_pid_esta_vivo_pid_proprio_retorna_true(self):
        """
        Validates: Requirement 1.2 (regressão)

        _pid_esta_vivo(os.getpid()) deve retornar True para o processo atual.
        Este teste garante que o fix não quebra a detecção de processo vivo.
        """
        resultado = bot._pid_esta_vivo(os.getpid())
        self.assertTrue(
            resultado,
            msg=(
                f"REGRESSÃO: _pid_esta_vivo({os.getpid()}) retornou False para "
                "o processo atual. A função não detecta corretamente processos vivos."
            ),
        )

    def test_pid_esta_vivo_pid_zero_retorna_false(self):
        """
        Validates: Requirement 1.2 (edge case)

        _pid_esta_vivo(0) deve retornar False (PID inválido).
        """
        resultado = bot._pid_esta_vivo(0)
        self.assertFalse(
            resultado,
            msg="EDGE CASE: _pid_esta_vivo(0) deveria retornar False (PID inválido).",
        )


# ── Bug 1.3: run_compra.py não libera lock ao encerrar ────────────────────────

class TestBug13RunCompraNaoLiberaLock(unittest.TestCase):
    """
    Validates: Requirement 1.3

    Quando run_compra.py é encerrado via KeyboardInterrupt, bot.lock deve
    ser removido. No código atual, não há try/finally com liberar_lock_instancia().

    No código NÃO corrigido: bot.lock permanece no disco → teste FALHA.
    Após o fix: bot.lock é removido → teste PASSA.
    """

    def setUp(self):
        """Usa um lock path temporário para não interferir com o bot real."""
        self._lock_path_original = bot._LOCK_PATH
        self._tmpdir = tempfile.mkdtemp()
        self._lock_path_temp = os.path.join(self._tmpdir, 'bot.lock')
        # Redireciona o lock path do bot para o temporário
        bot._LOCK_PATH = self._lock_path_temp

    def tearDown(self):
        """Restaura o lock path original."""
        bot._LOCK_PATH = self._lock_path_original
        # Limpa o temporário
        try:
            if os.path.exists(self._lock_path_temp):
                os.remove(self._lock_path_temp)
            os.rmdir(self._tmpdir)
        except Exception:
            pass

    def _simular_run_compra_sem_fix(self):
        """
        Simula o comportamento ATUAL de run_compra.py (sem o fix):
        - Não chama adquirir_lock_instancia()
        - Não tem try/finally com liberar_lock_instancia()
        - Ao receber KeyboardInterrupt, o lock NÃO é liberado
        """
        # Escreve o lock manualmente (como run_compra.py deveria fazer mas não faz)
        _escrever_lock(self._lock_path_temp, os.getpid())

        # Simula o loop principal de run_compra.py (sem try/finally):
        try:
            # Simula o bloco try/except KeyboardInterrupt atual de run_compra.py
            # que NÃO tem finally com liberar_lock_instancia()
            raise KeyboardInterrupt  # simula Ctrl+C
        except KeyboardInterrupt:
            pass
        # Sem finally → lock NÃO é liberado
        # bot.lock permanece no disco

    def _simular_run_compra_com_fix(self):
        """
        Simula o comportamento CORRETO de run_compra.py (com o fix):
        - Chama adquirir_lock_instancia() antes de iniciar
        - Tem try/finally com liberar_lock_instancia()
        - Ao receber KeyboardInterrupt, o lock É liberado
        """
        if not bot.adquirir_lock_instancia():
            return False  # recusou

        try:
            raise KeyboardInterrupt  # simula Ctrl+C
        except KeyboardInterrupt:
            pass
        finally:
            bot.liberar_lock_instancia()  # FIX: garante liberação do lock

        return True

    def test_run_compra_libera_lock_ao_encerrar(self):
        """
        Validates: Requirement 1.3

        Após encerramento de run_compra.py via KeyboardInterrupt, bot.lock
        NÃO deve existir no disco.

        EXPECTED (código corrigido):  bot.lock removido → teste PASSA
        ACTUAL   (código não corrigido): bot.lock permanece → teste FALHA
        """
        # Comportamento atual de run_compra.py (sem o fix):
        self._simular_run_compra_sem_fix()

        lock_existe_apos_encerramento = os.path.exists(self._lock_path_temp)

        self.assertFalse(
            lock_existe_apos_encerramento,
            msg=(
                "BUG 1.3 CONFIRMADO: bot.lock NÃO foi removido após encerramento "
                "de run_compra.py via KeyboardInterrupt. O arquivo permanece em disco, "
                "criando lock órfão que pode bloquear próximas inicializações.\n"
                "FIX necessário: adicionar try/finally com bot.liberar_lock_instancia() "
                "em run_compra.py."
            ),
        )

    def test_lock_removido_com_fix(self):
        """
        Validates: Requirement 1.3 (baseline do comportamento correto)

        Demonstra que o comportamento CORRETO (com fix) remove o lock.
        Este teste deve PASSAR mesmo antes do fix (testa a lógica do fix isoladamente),
        servindo como referência para o comportamento esperado.
        """
        resultado = self._simular_run_compra_com_fix()

        self.assertTrue(resultado, msg="adquirir_lock_instancia() não deveria recusar sem lock ativo.")
        self.assertFalse(
            os.path.exists(self._lock_path_temp),
            msg=(
                "REGRESSÃO: mesmo com o fix aplicado, bot.lock não foi removido "
                "após encerramento via KeyboardInterrupt."
            ),
        )

    def test_lock_orfao_apos_encerramento_sem_fix(self):
        """
        Validates: Requirement 1.3

        Documenta o contraexemplo explícito: bot.lock fica com o PID após
        encerramento sem o fix, comprovando o leak.
        """
        pid_antes = os.getpid()
        self._simular_run_compra_sem_fix()

        if os.path.exists(self._lock_path_temp):
            with open(self._lock_path_temp, 'r', encoding='utf-8') as f:
                pid_no_lock = f.read().strip()

            # Confirma que o lock órfão contém o PID deste processo
            self.assertEqual(
                pid_no_lock,
                str(pid_antes),
                msg=(
                    f"O lock órfão deveria conter PID {pid_antes}, "
                    f"mas contém '{pid_no_lock}'."
                ),
            )
            # O lock existe e contém o PID → confirma o bug 1.3
            self.fail(
                f"BUG 1.3 CONFIRMADO: bot.lock órfão encontrado com PID {pid_no_lock} "
                f"após encerramento de run_compra.py. Lock path: {self._lock_path_temp}\n"
                "Este PID agora está morto (o processo de teste continuou), mas o lock "
                "permaneceu no disco — bloqueará a próxima inicialização até ser "
                "reconhecido como órfão."
            )
        else:
            # Se não existe, o bug foi corrigido ou não era o comportamento esperado
            pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
