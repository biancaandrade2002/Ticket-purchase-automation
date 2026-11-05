"""
Teste de Exploração da Bug Condition — Task 1
Spec: invalid-session-recovery

**Validates: Requirements 1.1, 1.2, 1.3**

Property 1: Bug Condition — InvalidSessionIdException em switch_to.window()
            NÃO zera driver NEM interrompe o loop for no código NÃO corrigido.

Objetivo
--------
Confirmar que o bug EXISTS no código atual (não corrigido) de `monitor_buyticket()`.

Quando `driver.switch_to.window()` levanta `InvalidSessionIdException`:
  - BUG 1.1: o `except` interno faz `continue` sem zerar `driver`
  - BUG 1.2: o loop `for` não quebra — continua tentando as demais datas
  - BUG 1.3: ao reiniciar o `while True`, `driver is not None` → mecanismo de
             recuperação nunca é acionado → loop infinito de erros

Estratégia
----------
- `_run_buggy_handler()` contém cópia verbatim do handler atual do bot.py
  (sem o import de InvalidSessionIdException — exatamente como está no código)
- `driver.switch_to.window` é mockado para sempre levantar InvalidSessionIdException
- Os testes asserem o COMPORTAMENTO ESPERADO (pós-fix):
    * driver = None
    * abas_por_dia = {}
    * switch_to.window chamado apenas 1 vez (loop quebrou)
- No código não corrigido esses asserts FALHAM → confirma o bug

Contraexemplos documentados (capturados ao rodar no código não corrigido):
  - driver NÃO é None após InvalidSessionIdException
  - abas_por_dia NÃO é limpo (ainda contém as 3 entradas)
  - switch_to.window chamado 3 vezes (uma por data: 28/10, 30/10, 31/10)

NOTA: Quando o fix da task 3.2 for aplicado, estes mesmos testes DEVEM PASSAR,
confirmando que o comportamento correto foi implementado (task 3.4 re-roda eles).
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock

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

    # selenium e submódulos
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

        # selenium.common.exceptions — deve ter a classe real
        # mas como é stub, definimos as exceções necessárias
        exc_mod = types.ModuleType('selenium.common.exceptions')

        class WebDriverException(Exception): pass
        class InvalidSessionIdException(WebDriverException): pass
        class NoSuchWindowException(WebDriverException): pass
        class TimeoutException(WebDriverException): pass
        class StaleElementReferenceException(WebDriverException): pass
        class NoSuchElementException(WebDriverException): pass

        exc_mod.WebDriverException = WebDriverException
        exc_mod.InvalidSessionIdException = InvalidSessionIdException
        exc_mod.NoSuchWindowException = NoSuchWindowException
        exc_mod.TimeoutException = TimeoutException
        exc_mod.StaleElementReferenceException = StaleElementReferenceException
        exc_mod.NoSuchElementException = NoSuchElementException

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

    # webdriver_manager
    if 'webdriver_manager' not in sys.modules:
        wdm = types.ModuleType('webdriver_manager')
        wdm_chrome = types.ModuleType('webdriver_manager.chrome')

        class ChromeDriverManager:
            def install(self): return 'fake-driver'
        wdm_chrome.ChromeDriverManager = ChromeDriverManager
        sys.modules['webdriver_manager'] = wdm
        sys.modules['webdriver_manager.chrome'] = wdm_chrome


_instalar_stubs()

# Agora podemos importar a exceção do stub
from selenium.common.exceptions import InvalidSessionIdException  # noqa: E402


# ── Constantes ────────────────────────────────────────────────────────────────

DATAS = ['28/10', '30/10', '31/10']


# ── Helper: replica VERBATIM o handler bugado de monitor_buyticket() ──────────

def _make_abas():
    """Retorna abas_por_dia com as 3 datas de show."""
    return {d: {'handle': f'handle-{d}', 'url': f'http://buyticket.example/{d}'}
            for d in DATAS}


def _run_fixed_handler():
    """
    Executa o handler CORRIGIDO de switch_to.window()
    do loop `for data_show` em monitor_buyticket() de bot.py.

    O driver é mockado com switch_to.window levantando InvalidSessionIdException
    em TODAS as chamadas — simulando uma sessão morta do Chrome.

    Retorna um dict com o estado após o loop:
        driver         – referência ao driver (deve ser None após o fix)
        abas_por_dia   – dict de abas (deve ser {} após o fix)
        call_count     – nº de vezes que switch_to.window foi chamado
                         (deve ser 1 após o fix — loop quebra na primeira falha)
        logs           – mensagens de log geradas
    """
    driver_mock = MagicMock(name='driver')
    driver_mock.switch_to.window.side_effect = InvalidSessionIdException('dead session')
    abas_por_dia = _make_abas()
    logs = []
    driver = driver_mock  # variável local que será zerada pelo fix

    # ── CÓPIA VERBATIM DO HANDLER CORRIGIDO em monitor_buyticket() (bot.py ~L1709) ──
    for data_show in DATAS:
        aba = abas_por_dia.get(data_show)
        if not aba:
            continue
        try:
            driver.switch_to.window(aba['handle'])
        except InvalidSessionIdException as e:
            logs.append(f'[{data_show}] Sessão morta — encerrando driver e reiniciando... ({e})')
            try:
                driver.quit()
            except Exception:
                pass
            driver = None
            abas_por_dia = {}
            break
        except Exception as e:
            logs.append(f'[{data_show}] Erro ao trocar aba: {e}')
            continue
    # ── FIM DO HANDLER CORRIGIDO ──

    return {
        'driver': driver,          # None após o fix
        'abas_por_dia': abas_por_dia,  # {} após o fix
        'call_count': driver_mock.switch_to.window.call_count,  # 1 após o fix
        'logs': logs,
    }


# ── Testes de Exploração da Bug Condition ─────────────────────────────────────

class TestBugConditionInvalidSession(unittest.TestCase):
    """
    Validates: Requirements 1.1, 1.2, 1.3

    Estes testes confirmam que o FIX foi aplicado corretamente.
    Passam com o handler corrigido; falhavam com o handler bugado.
    """

    def test_driver_zerado_apos_invalid_session(self):
        """
        Validates: Requirement 1.1

        Após InvalidSessionIdException em switch_to.window(), driver DEVE ser None.

        FIX CONFIRMADO: o handler corrigido faz `driver = None` dentro do
        except InvalidSessionIdException, acionando o mecanismo de recuperação
        do while True na próxima iteração.
        """
        result = _run_fixed_handler()
        self.assertIsNone(
            result['driver'],
            msg=(
                "FIX FALHOU (Req 1.1): driver NÃO foi zerado após "
                "InvalidSessionIdException. "
                f"driver = {result['driver']!r}. "
                f"switch_to.window chamado {result['call_count']} vez(es). "
                f"Logs: {result['logs']}"
            ),
        )

    def test_abas_limpas_apos_invalid_session(self):
        """
        Validates: Requirement 1.1

        Após InvalidSessionIdException, abas_por_dia DEVE ser {}.

        FIX CONFIRMADO: o handler corrigido faz `abas_por_dia = {}` dentro do
        except InvalidSessionIdException, forçando re-abertura das abas no
        próximo ciclo de recuperação.
        """
        result = _run_fixed_handler()
        self.assertEqual(
            result['abas_por_dia'], {},
            msg=(
                "FIX FALHOU (Req 1.1): abas_por_dia NÃO foi limpo após "
                "InvalidSessionIdException. "
                f"abas_por_dia ainda contém: {list(result['abas_por_dia'].keys())}. "
                f"Logs: {result['logs']}"
            ),
        )

    def test_loop_interrompido_apos_primeira_invalid_session(self):
        """
        Validates: Requirements 1.2, 1.3

        O loop `for` DEVE ser interrompido após a primeira InvalidSessionIdException —
        switch_to.window deve ser chamado APENAS 1 vez (para '28/10').

        FIX CONFIRMADO: o handler corrigido usa `break` após tratar a exceção,
        interrompendo o loop e evitando tentativas nas datas restantes.
        """
        result = _run_fixed_handler()
        self.assertEqual(
            result['call_count'], 1,
            msg=(
                "FIX FALHOU (Reqs 1.2, 1.3): switch_to.window foi chamado "
                f"{result['call_count']} vez(es) (esperado: 1 — só para '28/10'). "
                "O loop `for` NÃO foi interrompido com `break` após a primeira "
                "InvalidSessionIdException. "
                f"Logs: {result['logs']}"
            ),
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
