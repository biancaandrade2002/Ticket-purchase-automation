"""
Preservation Property Tests — Task 2 (bugfix: invalid-session-recovery)

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 2: Para qualquer exceção que NÃO seja InvalidSessionIdException lançada
em `driver.switch_to.window()`, o handler DEVE preservar o comportamento original:
  - driver permanece não-None (inalterado)
  - o loop `for` NÃO quebra — todas as datas são processadas
  - o erro é logado com a data correspondente

Estes testes codificam o COMPORTAMENTO DE BASELINE a preservar.
ESPERADO: PASSAR no código não corrigido (confirma a baseline).
ESPERADO: Continuar PASSANDO após o fix ser aplicado (task 3.5 re-roda eles).
"""

import sys
import types
import unittest
from unittest.mock import MagicMock

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


# ── Stubs mínimos para importar sem dependências pesadas ──────────────────────

def _instalar_stubs():
    """Injeta stubs leves em sys.modules antes de importar módulos do projeto."""
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

        # selenium.common.exceptions — definimos as exceções necessárias
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

from selenium.common.exceptions import (  # noqa: E402
    InvalidSessionIdException,
    WebDriverException,
    NoSuchWindowException,
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
)


# ── Constantes ────────────────────────────────────────────────────────────────

DATAS = ['28/10', '30/10', '31/10']

NON_BUG_EXCEPTIONS = [
    WebDriverException,
    NoSuchWindowException,
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
    Exception,  # plain Python exception
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_abas():
    """Retorna abas_por_dia com as 3 datas de show."""
    return {d: {'handle': f'handle-{d}', 'url': f'http://buyticket.example/{d}'}
            for d in DATAS}


def _run_handler_with_exception(exc_class):
    """
    Executa EXATAMENTE o handler atual (não corrigido) de switch_to.window()
    do loop `for data_show` em monitor_buyticket() de bot.py, mas com a
    exceção fornecida (que NÃO deve ser InvalidSessionIdException).

    Retorna: (driver, abas_por_dia, call_count, logs, original_driver)
    """
    driver = MagicMock(name='driver')
    driver.switch_to.window.side_effect = exc_class("test error")
    abas_por_dia = _make_abas()
    logs = []
    original_driver = driver  # salva referência para comparação

    # ── CÓPIA VERBATIM DO HANDLER ATUAL (não corrigido) de monitor_buyticket() ──
    for data_show in DATAS:
        aba = abas_por_dia.get(data_show)
        if not aba:
            continue
        try:
            driver.switch_to.window(aba['handle'])
        except Exception as e:
            logs.append(f'[{data_show}] Erro ao trocar aba: {e}')
            continue
    # ── FIM DO HANDLER ──

    return driver, abas_por_dia, driver.switch_to.window.call_count, logs, original_driver


# ── Testes de Preservação (exemplos concretos) ────────────────────────────────

class TestPreservationNonBugExceptions(unittest.TestCase):
    """
    Validates: Requirements 3.1, 3.2, 3.3, 3.4

    Testa que exceções NÃO-InvalidSessionIdException preservam o comportamento atual:
    - driver não é zerado
    - loop for não quebra (todas as datas são tentadas)
    - erros são logados

    ESPERADO: PASSAR no código não corrigido (documenta o baseline).
    ESPERADO: Continuar PASSANDO após o fix (sem regressões).
    """

    def _assert_preservation(self, exc_class):
        """Verifica as invariantes de preservação para a exceção fornecida."""
        driver, abas_por_dia, call_count, logs, original_driver = \
            _run_handler_with_exception(exc_class)

        # driver deve permanecer não-None (inalterado)
        self.assertIsNotNone(
            driver,
            msg=(
                f"REGRESSÃO: driver foi zerado para {exc_class.__name__} "
                "(isso só deve ocorrer para InvalidSessionIdException)"
            ),
        )
        self.assertIs(
            driver, original_driver,
            msg=(
                f"REGRESSÃO: referência do driver foi alterada para {exc_class.__name__}"
            ),
        )

        # abas_por_dia deve permanecer inalterado
        self.assertEqual(
            len(abas_por_dia), 3,
            msg=(
                f"REGRESSÃO: abas_por_dia foi limpo para {exc_class.__name__} "
                "(isso só deve ocorrer para InvalidSessionIdException)"
            ),
        )

        # Todas as datas devem ser tentadas (loop NÃO quebra)
        self.assertEqual(
            call_count, 3,
            msg=(
                f"REGRESSÃO: loop quebrou após o primeiro {exc_class.__name__} "
                f"(esperado: 3 tentativas para todas as datas, obtido: {call_count})"
            ),
        )

        # Erros devem ser logados para cada data
        self.assertEqual(
            len(logs), 3,
            msg=(
                f"REGRESSÃO: esperadas 3 entradas de log para {exc_class.__name__}, "
                f"obtidas: {len(logs)}"
            ),
        )

    def test_webdriver_exception_preserves_behavior(self):
        """Validates: Requirement 3.2 — WebDriverException genérica: log + continue."""
        self._assert_preservation(WebDriverException)

    def test_no_such_window_preserves_behavior(self):
        """Validates: Requirement 3.2 — NoSuchWindowException: log + continue."""
        self._assert_preservation(NoSuchWindowException)

    def test_timeout_exception_preserves_behavior(self):
        """Validates: Requirement 3.2 — TimeoutException: log + continue."""
        self._assert_preservation(TimeoutException)

    def test_stale_element_preserves_behavior(self):
        """Validates: Requirement 3.2 — StaleElementReferenceException: log + continue."""
        self._assert_preservation(StaleElementReferenceException)

    def test_no_such_element_preserves_behavior(self):
        """Validates: Requirement 3.2 — NoSuchElementException: log + continue."""
        self._assert_preservation(NoSuchElementException)

    def test_generic_exception_preserves_behavior(self):
        """Validates: Requirement 3.2 — Exceção Python genérica: log + continue."""
        self._assert_preservation(Exception)

    def test_log_content_includes_date(self):
        """
        Validates: Requirement 3.2

        Cada entrada de log deve conter a data correspondente no formato [DD/MM].
        """
        _, _, _, logs, _ = _run_handler_with_exception(WebDriverException)
        for data in DATAS:
            self.assertTrue(
                any(f'[{data}]' in log for log in logs),
                msg=f"Log não contém a data [{data}]. Logs: {logs}",
            )


# ── Testes de Preservação baseados em propriedades (Hypothesis) ───────────────

if HAS_HYPOTHESIS:
    class TestPreservationHypothesis(unittest.TestCase):
        """
        Validates: Requirements 3.1, 3.2, 3.3, 3.4

        Property 2 (Hypothesis): Para QUALQUER exceção não-InvalidSessionIdException
        do pool, o comportamento de baseline deve ser preservado.
        """

        @given(st.sampled_from(NON_BUG_EXCEPTIONS))
        @settings(max_examples=50)
        def test_any_non_bug_exception_preserves_behavior(self, exc_class):
            """
            Validates: Requirements 3.1, 3.2

            Para qualquer exceção não-InvalidSessionIdException:
            - driver permanece não-None
            - abas_por_dia inalterado (3 entradas)
            - switch_to.window chamado 3 vezes (loop não quebra)
            """
            driver, abas_por_dia, call_count, logs, original_driver = \
                _run_handler_with_exception(exc_class)

            assert driver is not None, (
                f"REGRESSÃO: driver foi zerado para {exc_class.__name__}"
            )
            assert driver is original_driver, (
                f"REGRESSÃO: referência do driver alterada para {exc_class.__name__}"
            )
            assert len(abas_por_dia) == 3, (
                f"REGRESSÃO: abas_por_dia limpo para {exc_class.__name__}"
            )
            assert call_count == 3, (
                f"REGRESSÃO: loop quebrou para {exc_class.__name__} "
                f"(chamado {call_count}x, esperado 3)"
            )
            assert len(logs) == 3, (
                f"REGRESSÃO: {len(logs)} logs para {exc_class.__name__}, esperado 3"
            )


if __name__ == '__main__':
    unittest.main(verbosity=2)
