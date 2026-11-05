"""
Integration Tests — Bot Speed Optimization (Tasks 5.1–5.5).

These tests require:
  - Google Chrome installed and in PATH.
  - A valid `.env` file at the project root with at minimum:
      BUYTICKET_EMAIL, BUYTICKET_SENHA, NTFY_TOPIC

Run integration tests explicitly:
    py -m pytest tests/test_bot_integration.py -v -m integration

Unit/property tests are NOT affected: the integration mark keeps these tests
skipped when the full suite is run without `-m integration`.

All tests share a single Chrome session via the `live_driver` module-scoped
fixture, which performs login and opens the three persistent day-tabs once.
The session is torn down after all tests in the module finish.
"""

import os
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# Adjust sys.path so `bot` and `ticket_logic` are importable from the project
# root regardless of where pytest is invoked.
# ---------------------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot
from selenium.webdriver.common.by import By

# ---------------------------------------------------------------------------
# Register custom mark (prevents PytestUnknownMarkWarning).
# The marker is also declared in conftest.py if it exists.
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.integration


# ===========================================================================
# Fixture — shared Chrome session (module scope)
# ===========================================================================

@pytest.fixture(scope='module')
def live_driver():
    """
    Creates a real Chrome session, logs in to BuyTicket, opens one persistent
    tab per show day, and yields (driver, abas_por_dia).

    Skipped automatically when the required environment variables are absent,
    so the fixture is safe to import in any environment.
    """
    # Guard: skip when credentials are not configured.
    email = os.getenv('BUYTICKET_EMAIL')
    senha = os.getenv('BUYTICKET_SENHA')
    ntfy  = os.getenv('NTFY_TOPIC')
    if not email or not senha or not ntfy:
        pytest.skip(
            'Integration tests require BUYTICKET_EMAIL, BUYTICKET_SENHA '
            'and NTFY_TOPIC in the .env file.'
        )

    # Guard: skip when ChromeDriver cannot be resolved (CI without Chrome).
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        bot.CHROMEDRIVER_PATH = ChromeDriverManager().install()
    except Exception as exc:
        pytest.skip(f'ChromeDriver not available: {exc}')

    driver = bot.criar_driver()
    try:
        bot.buyticket_login(driver)
        abas = bot.abrir_abas_por_dia(driver, bot.URLS_BUYTICKET, bot.DATAS_SHOW)
        yield driver, abas
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ===========================================================================
# Test 5.1 — Login redirects away from /entrar
# Validates: Requirements 1.1
# ===========================================================================

@pytest.mark.integration
def test_login_redireciona_para_fora_do_entrar(live_driver):
    """
    **Validates: Requirements 1.1**

    After a successful login, the current URL must NOT contain 'entrar'.
    If the session landed on the login page the bot would loop endlessly.
    """
    driver, abas = live_driver
    # Switch to the first day-tab (guaranteed to exist after fixture setup).
    first_handle = list(abas.values())[0]['handle']
    driver.switch_to.window(first_handle)
    assert 'entrar' not in driver.current_url.lower(), (
        f'Expected to be past the login page but URL is: {driver.current_url}'
    )


# ===========================================================================
# Test 5.2 — Three persistent tabs with distinct handles
# Validates: Requirements 1.1
# ===========================================================================

@pytest.mark.integration
def test_abertura_tres_abas_handles_distintos(live_driver):
    """
    **Validates: Requirements 1.1**

    `abrir_abas_por_dia` must return a dict with exactly the three expected
    show-date keys, and each tab must have a unique window handle.
    """
    driver, abas = live_driver
    assert set(abas.keys()) == {'28/10', '30/10', '31/10'}, (
        f'Expected keys {{28/10, 30/10, 31/10}}, got {set(abas.keys())}'
    )
    handles = [abas[d]['handle'] for d in ['28/10', '30/10', '31/10']]
    assert len(set(handles)) == 3, (
        f'Expected 3 distinct tab handles, got {handles}'
    )


# ===========================================================================
# Test 5.3 — buyticket_sessao_ativa returns bool and True after fresh login
# Validates: Requirements 1.7
# ===========================================================================

@pytest.mark.integration
def test_sessao_ativa_retorna_bool(live_driver):
    """
    **Validates: Requirements 1.7**

    `buyticket_sessao_ativa` must always return a plain bool.
    Right after a fresh login the session is expected to be active (True).
    """
    driver, abas = live_driver
    # Switch back to a known day-tab before the check so the function can
    # open its auxiliary tab without conflicts.
    first_handle = list(abas.values())[0]['handle']
    driver.switch_to.window(first_handle)

    resultado = bot.buyticket_sessao_ativa(driver)

    assert isinstance(resultado, bool), (
        f'buyticket_sessao_ativa must return bool, got {type(resultado)}'
    )
    # After a just-completed login the session should still be active.
    assert resultado is True, (
        'Session should be active immediately after login'
    )


# ===========================================================================
# Test 5.4 — CrowdHandler queue detected by #ch-active selector
# Validates: Requirements 3.4
# ===========================================================================

@pytest.mark.integration
def test_fila_crowdhandler_detectada_por_seletor(live_driver):
    """
    **Validates: Requirements 3.4**

    `_em_fila_de_espera` must return True when a page containing a visible
    `section#ch-active` element is loaded, matching the real CrowdHandler DOM.
    """
    driver, abas = live_driver
    html = (
        '<html><body>'
        '<section id="ch-active" style="display:block">Em fila</section>'
        '</body></html>'
    )
    driver.get(f'data:text/html,{html}')
    time.sleep(0.5)

    assert bot._em_fila_de_espera(driver) is True, (
        '_em_fila_de_espera should detect section#ch-active when visible'
    )

    # Cleanup: navigate back to the first day-tab so subsequent tests are not
    # left on the data: URL.
    first_handle = list(abas.values())[0]['handle']
    driver.switch_to.window(first_handle)


# ===========================================================================
# Test 5.5 — buyticket_preencher_dados fills inputs correctly
# Validates: Requirements 6.1, 6.2, 6.3
# ===========================================================================

@pytest.mark.integration
def test_preenchimento_dados_campos_corretos(live_driver):
    """
    **Validates: Requirements 6.1, 6.2, 6.3**

    `buyticket_preencher_dados` must fill at least the non-masked fields
    (Nome completo) and attempt to fill the masked ones (Telefone, CPF).
    We verify that at least one input receives a non-empty value, and that
    the nome field specifically gets the expected value.
    """
    driver, abas = live_driver

    # Minimal checkout-like HTML with one masked and two non-masked fields.
    html = (
        '<html><body>'
        '<input id="telefone" placeholder="Telefone celular">'
        '<input id="cpf"      placeholder="CPF/CNPJ">'
        '<input id="nome"     placeholder="Nome completo ">'
        '</body></html>'
    )
    driver.get(f'data:text/html,{html}')
    time.sleep(0.5)

    # Override module-level variables so the function has values to fill.
    original_telefone    = bot.TELEFONE
    original_cpf         = bot.CPF
    original_nome        = bot.NOME_COMPLETO

    bot.TELEFONE     = '11999998888'
    bot.CPF          = '12345678901'
    bot.NOME_COMPLETO = 'ARMY Test'

    try:
        bot.buyticket_preencher_dados(driver)
        time.sleep(1)

        inputs  = driver.find_elements(By.CSS_SELECTOR, 'input')
        values  = {inp.get_attribute('id'): inp.get_attribute('value') for inp in inputs}

        # At least one field must have been filled.
        filled = [v for v in values.values() if v]
        assert filled, (
            f'No input was filled. Values found: {values}'
        )

        # The non-masked "Nome completo" field should contain the expected value.
        assert values.get('nome') == 'ARMY Test', (
            f'Expected nome="ARMY Test", got {values.get("nome")!r}'
        )
    finally:
        # Restore original module-level values so other tests are unaffected.
        bot.TELEFONE      = original_telefone
        bot.CPF           = original_cpf
        bot.NOME_COMPLETO = original_nome

        # Navigate back to first day-tab.
        first_handle = list(abas.values())[0]['handle']
        driver.switch_to.window(first_handle)
