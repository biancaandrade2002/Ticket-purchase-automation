"""
Teste de Exploração da Bug Condition — Task 1
Spec: tm-monitor-login

**Validates: Requirements 1.1, 1.2, 1.3**

Property 1: Bug Condition — Autenticação Obrigatória na Inicialização

Objetivo
--------
Surfaçar contraexemplos que demonstrem que `monitor_ticketmaster()` opera
sem autenticação no Ticketmaster.

IMPORTANT — Metodologia do bugfix workflow:
  Estes testes codificam o COMPORTAMENTO ESPERADO após o fix.
  Eles DEVEM FALHAR no código NÃO corrigido — essa falha confirma que o bug existe.
  Após o fix (Task 3), todos os testes DEVEM PASSAR.
  NÃO tente corrigir o código nem o teste quando ele falhar.

Contraexemplos esperados (confirmam o bug):
  1. 'ticketmaster_login' não existe em bot.py (função nunca implementada)
  2. '_checar_login_periodico_tm' não existe em bot.py (helper ausente)
  3. Source de monitor_ticketmaster() não chama 'ticketmaster_login' antes
     de '_abrir_abas()' — driver criado anonimamente
  4. Source de monitor_ticketmaster() não declara 'ultima_checagem_login_tm'
     — nenhuma checagem periódica de sessão
  5. Source de monitor_ticketmaster() não verifica '/login' em current_url
     após refresh — redirect de sessão expirada passa silenciosamente
  6. Execução real com mocks: 'ticketmaster_login' nunca entra no call_log
     durante inicialização do driver
"""

import sys
import os
import types
import inspect
import unittest
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# Stubs de dependências pesadas (instalados ANTES de importar bot)
# ─────────────────────────────────────────────────────────────────────────────

def _instalar_stubs():
    """Injeta stubs leves em sys.modules antes de importar bot."""
    stubs_simples = {
        'requests': ('post',),
        'dotenv': ('load_dotenv',),
    }
    for mod_name, attrs in stubs_simples.items():
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

    if 'webdriver_manager' not in sys.modules:
        wdm = types.ModuleType('webdriver_manager')
        wdm_chrome = types.ModuleType('webdriver_manager.chrome')

        class ChromeDriverManager:
            def install(self): return 'fake-driver'
        wdm_chrome.ChromeDriverManager = ChromeDriverManager
        sys.modules['webdriver_manager'] = wdm
        sys.modules['webdriver_manager.chrome'] = wdm_chrome


_instalar_stubs()

# Adiciona o diretório raiz ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel para escapar do while True de monitor_ticketmaster()
# BaseException ignora todos os `except Exception:` do bot.
# ─────────────────────────────────────────────────────────────────────────────

class _StopMonitor(BaseException):
    """Usado para escapar do while True sem ser capturado pelo except Exception."""


# ─────────────────────────────────────────────────────────────────────────────
# Testes de Exploração — Property 1: Bug Condition
# ─────────────────────────────────────────────────────────────────────────────

class TestTMBugConditionExploration(unittest.TestCase):
    """
    Validates: Requirements 1.1, 1.2, 1.3

    Testes de exploração da bug condition:
      isBugCondition = (driver criado sem ticketmaster_login) OR
                       (deve_checar_login == True AND sessao não verificada)

    Estes testes codificam o comportamento ESPERADO após o fix.
    NO CÓDIGO NÃO CORRIGIDO: TODOS OS TESTES DEVEM FALHAR.
    APÓS O FIX: todos os testes devem PASSAR.
    """

    # ── Cenário 1: funções de autenticação TM existem? ────────────────────────

    def test_1_ticketmaster_login_funcao_deve_existir(self):
        """
        Validates: Requirement 1.1

        'ticketmaster_login' DEVE existir em bot.py.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          AttributeError — bot não tem 'ticketmaster_login'.
          A função de autenticação TM nunca foi implementada; sem ela,
          monitor_ticketmaster() não tem como autenticar o driver.

        FALHA ESPERADA: AssertionError — confirma que a função está ausente.
        """
        self.assertTrue(
            hasattr(bot, 'ticketmaster_login'),
            msg=(
                "BUG CONFIRMADO (Req 1.1): 'ticketmaster_login' NÃO existe em bot.py.\n"
                "A função de autenticação TM nunca foi implementada.\n"
                "Contraexemplo: bot não tem atributo 'ticketmaster_login'.\n"
                "Compare com 'buyticket_login' que existe e é chamada em _monitor_dia()."
            ),
        )

    def test_2_checar_login_periodico_tm_deve_existir(self):
        """
        Validates: Requirement 1.2

        '_checar_login_periodico_tm' DEVE existir em bot.py.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          bot não tem '_checar_login_periodico_tm'.
          Sem este helper, a sessão TM nunca é verificada periodicamente —
          ao contrário do BuyTicket que tem '_checar_login_periodico()'.

        FALHA ESPERADA: AssertionError — confirma que o orquestrador periódico está ausente.
        """
        self.assertTrue(
            hasattr(bot, '_checar_login_periodico_tm'),
            msg=(
                "BUG CONFIRMADO (Req 1.2): '_checar_login_periodico_tm' NÃO existe "
                "em bot.py.\n"
                "Sem este helper, a sessão TM nunca é verificada periodicamente.\n"
                "Contraexemplo: bot não tem atributo '_checar_login_periodico_tm'.\n"
                "Compare com '_checar_login_periodico' (BuyTicket, linha 2763 de bot.py)."
            ),
        )

    def test_3_ticketmaster_sessao_ativa_deve_existir(self):
        """
        Validates: Requirement 1.2

        'ticketmaster_sessao_ativa' DEVE existir em bot.py.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          bot não tem 'ticketmaster_sessao_ativa'.
          Sem esta função, não é possível verificar se a sessão TM está válida.

        FALHA ESPERADA: AssertionError — confirma que a função de verificação está ausente.
        """
        self.assertTrue(
            hasattr(bot, 'ticketmaster_sessao_ativa'),
            msg=(
                "BUG CONFIRMADO (Req 1.2): 'ticketmaster_sessao_ativa' NÃO existe "
                "em bot.py.\n"
                "Contraexemplo: bot não tem atributo 'ticketmaster_sessao_ativa'.\n"
                "Compare com 'buyticket_sessao_ativa' que existe (linha 817 de bot.py)."
            ),
        )

    # ── Cenário 2: source de monitor_ticketmaster() tem as chamadas corretas? ──

    def test_4_source_chama_ticketmaster_login(self):
        """
        Validates: Requirement 1.1

        O source de monitor_ticketmaster() DEVE conter 'ticketmaster_login'.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          'ticketmaster_login' não aparece em nenhum lugar do source.
          O bloco `if driver is None:` só contém criar_driver() e _abrir_abas().

        FALHA ESPERADA: AssertionError — 'ticketmaster_login' ausente do source.
        """
        source = inspect.getsource(bot.monitor_ticketmaster)

        self.assertIn(
            'ticketmaster_login',
            source,
            msg=(
                "BUG CONFIRMADO (Req 1.1): 'ticketmaster_login' NÃO aparece no source "
                "de monitor_ticketmaster().\n"
                "Contraexemplo: o bloco 'if driver is None:' contém apenas "
                "'criar_driver()' e '_abrir_abas()' sem nenhuma autenticação.\n"
                "Compare com _monitor_dia() que chama 'buyticket_login(driver)' "
                "antes de '_abrir_aba_dia()'."
            ),
        )

    def test_5_ticketmaster_login_antes_de_abrir_abas(self):
        """
        Validates: Requirement 1.1

        No source de monitor_ticketmaster(), 'ticketmaster_login' DEVE aparecer
        ANTES de 'handles = _abrir_abas()'.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          'ticketmaster_login' não existe no source OU aparece depois — o driver
          navega para URLs de evento antes de qualquer autenticação.

        FALHA ESPERADA: AssertionError — login ausente ou fora de ordem.
        """
        source = inspect.getsource(bot.monitor_ticketmaster)

        # Primeiro verifica que ticketmaster_login aparece no source
        if 'ticketmaster_login' not in source:
            self.fail(
                "BUG CONFIRMADO (Req 1.1): 'ticketmaster_login' não aparece no source "
                "de monitor_ticketmaster(). Driver criado sem autenticação prévia.\n"
                "Contraexemplo: bloco 'if driver is None:' → criar_driver() → "
                "_abrir_abas() sem nenhum ticketmaster_login() entre eles."
            )

        login_pos = source.find('ticketmaster_login')
        abrir_abas_pos = source.find('handles = _abrir_abas()')

        self.assertNotEqual(
            abrir_abas_pos, -1,
            msg="'handles = _abrir_abas()' não encontrado no source de monitor_ticketmaster().",
        )

        self.assertLess(
            login_pos, abrir_abas_pos,
            msg=(
                "BUG CONFIRMADO (Req 1.1): 'ticketmaster_login' aparece DEPOIS de "
                "'handles = _abrir_abas()' no source de monitor_ticketmaster().\n"
                f"Posição de ticketmaster_login: {login_pos}, "
                f"Posição de _abrir_abas(): {abrir_abas_pos}.\n"
                "A autenticação deve ocorrer ANTES da abertura das abas de evento."
            ),
        )

    def test_6_ultima_checagem_login_tm_declarada(self):
        """
        Validates: Requirement 1.2

        O source de monitor_ticketmaster() DEVE declarar 'ultima_checagem_login_tm'.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          'ultima_checagem_login_tm' não existe no source.
          O loop while True não tem nenhum contador de timestamp de última
          verificação de sessão TM.

        FALHA ESPERADA: AssertionError — variável ausente do source.
        """
        source = inspect.getsource(bot.monitor_ticketmaster)

        self.assertIn(
            'ultima_checagem_login_tm',
            source,
            msg=(
                "BUG CONFIRMADO (Req 1.2): 'ultima_checagem_login_tm' NÃO existe no "
                "source de monitor_ticketmaster().\n"
                "Contraexemplo: o while True não declara nem usa nenhum contador de "
                "timestamp de sessão TM.\n"
                "Compare com _monitor_dia() que declara "
                "'ultima_checagem_login = None' (linha 2812 de bot.py) e a atualiza "
                "a cada ciclo com '_checar_login_periodico()'."
            ),
        )

    def test_7_checagem_periodica_chamada_no_loop(self):
        """
        Validates: Requirement 1.2

        O source de monitor_ticketmaster() DEVE chamar '_checar_login_periodico_tm'
        dentro do loop principal.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          '_checar_login_periodico_tm' não existe nem é chamada.

        FALHA ESPERADA: AssertionError — chamada periódica ausente.
        """
        source = inspect.getsource(bot.monitor_ticketmaster)

        self.assertIn(
            '_checar_login_periodico_tm',
            source,
            msg=(
                "BUG CONFIRMADO (Req 1.2): '_checar_login_periodico_tm' NÃO é chamada "
                "no loop de monitor_ticketmaster().\n"
                "Contraexemplo: o while True não verifica a sessão periodicamente.\n"
                "Compare com _monitor_dia() que chama "
                "'ultima_checagem_login = _checar_login_periodico(driver, "
                "ultima_checagem_login)' a cada ciclo."
            ),
        )

    def test_8_redirect_login_detectado_apos_refresh(self):
        """
        Validates: Requirement 1.3

        O source de monitor_ticketmaster() DEVE verificar '/login' em
        driver.current_url após driver.refresh(), detectando sessão expirada.

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          Nenhuma verificação de '/login' em current_url após refresh.
          Uma aba redirecionada para /login é classificada como 'desconhecido'
          em vez de disparar re-login.

        FALHA ESPERADA: AssertionError — verificação de redirect ausente.
        """
        source = inspect.getsource(bot.monitor_ticketmaster)

        # Aceita qualquer variação de verificação de /login na URL atual
        has_login_redirect_check = (
            "'/login' in driver.current_url" in source
            or '"/login" in driver.current_url' in source
            or "'/login' in" in source and 'current_url' in source
        )

        self.assertTrue(
            has_login_redirect_check,
            msg=(
                "BUG CONFIRMADO (Req 1.3): monitor_ticketmaster() NÃO verifica se "
                "driver.current_url contém '/login' após driver.refresh().\n"
                "Contraexemplo: sessão expirada → refresh → redirect para /login → "
                "_tm_classificar() retorna 'desconhecido' sem disparar re-login.\n"
                "O código atual não tem nenhuma verificação de "
                "\"'/login' in driver.current_url\" no loop de polling."
            ),
        )

    # ── Cenário 3: execução real com mocks (call order) ───────────────────────

    def test_9_execucao_com_mocks_login_antes_de_abrir_abas(self):
        """
        Validates: Requirement 1.1

        Execução real de monitor_ticketmaster() com mocks: 'ticketmaster_login'
        DEVE ser chamada antes de qualquer driver.get() (que ocorre em _abrir_abas).

        Estratégia:
          - criar_driver() retorna um mock driver controlado
          - ticketmaster_login é injetada via monkeypatch (create=True)
          - driver.refresh() levanta _StopMonitor (BaseException) para parar
            o loop imediatamente após a fase de polling começar
          - Verifica call_log: ticketmaster_login deve aparecer antes de
            qualquer driver.get() e antes de driver.refresh()

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          call_log contém 'criar_driver' e 'driver.get' mas NÃO 'ticketmaster_login'.
          A função foi injetada mas o código nunca a chama.

        FALHA ESPERADA: AssertionError — 'ticketmaster_login' ausente do call_log.
        """
        call_log = []

        # Mock driver com window_handles para _abrir_abas funcionar
        mock_driver = MagicMock()
        mock_driver.window_handles = ['h0', 'h1', 'h2']
        mock_driver.current_window_handle = 'h0'
        mock_driver.page_source = '<html><body></body></html>'
        mock_driver.current_url = 'https://www.www.ticketmaster.com.br/event/28'

        def fake_criar_driver():
            call_log.append('criar_driver')
            return mock_driver

        def fake_ticketmaster_login(driver):
            call_log.append('ticketmaster_login')

        # _wait_for é chamado 3× em _abrir_abas (uma por URL); na 4ª chamada
        # (polling loop) paramos via _StopMonitor (BaseException, não capturado
        # pelo 'except Exception' do bot)
        wait_call_count = [0]

        def fake_wait_for(driver, selector, timeout=8, by=None):
            wait_call_count[0] += 1
            if wait_call_count[0] > 3:
                raise _StopMonitor('loop escapado após _abrir_abas')
            return True

        # driver.refresh() começa o polling — para imediatamente se chamado
        def fake_refresh():
            raise _StopMonitor('polling iniciado — parando')
        mock_driver.refresh = fake_refresh

        # Injeta ticketmaster_login (create=True porque não existe ainda)
        had_login = hasattr(bot, 'ticketmaster_login')
        original_login = getattr(bot, 'ticketmaster_login', None)
        bot.ticketmaster_login = fake_ticketmaster_login

        try:
            with patch.object(bot, 'criar_driver', fake_criar_driver), \
                 patch.object(bot, '_wait_for', fake_wait_for), \
                 patch.object(bot, 'notificar', lambda *a, **k: None), \
                 patch.object(bot, 'log', lambda *a, **k: None), \
                 patch('time.sleep', lambda *a, **k: None):
                try:
                    bot.monitor_ticketmaster()
                except _StopMonitor:
                    pass  # saída controlada — esperada
        finally:
            # Restaura estado original de bot.ticketmaster_login
            if had_login:
                bot.ticketmaster_login = original_login
            elif hasattr(bot, 'ticketmaster_login'):
                delattr(bot, 'ticketmaster_login')

        # Verificação principal: ticketmaster_login deve ter sido chamada
        self.assertIn(
            'ticketmaster_login',
            call_log,
            msg=(
                "BUG CONFIRMADO (Req 1.1): 'ticketmaster_login' NÃO foi chamada "
                "durante a inicialização do driver em monitor_ticketmaster().\n"
                f"Call log completo: {call_log}\n"
                "Contraexemplo: o código faz criar_driver() → _abrir_abas() sem "
                "nenhuma chamada a ticketmaster_login() entre eles.\n"
                "A função foi injetada via monkeypatch mas nunca foi invocada — "
                "o código simplesmente não a chama."
            ),
        )

        # Verificação de ordem: ticketmaster_login antes de driver.get (se presente)
        if 'ticketmaster_login' in call_log:
            login_idx = call_log.index('ticketmaster_login')
            criar_idx = call_log.index('criar_driver') if 'criar_driver' in call_log else -1

            # ticketmaster_login deve aparecer APÓS criar_driver
            if criar_idx != -1:
                self.assertGreater(
                    login_idx, criar_idx,
                    msg=(
                        "ORDEM INCORRETA: 'ticketmaster_login' foi chamada ANTES de "
                        "'criar_driver'. Login deve ocorrer DEPOIS da criação do driver.\n"
                        f"Call log: {call_log}"
                    ),
                )

    def test_10_sem_ultima_checagem_login_nao_ha_checagem_periodica(self):
        """
        Validates: Requirement 1.2

        Confirma via inspeção de código-fonte que _monitor_dia() tem
        'ultima_checagem_login' mas monitor_ticketmaster() NÃO tem equivalente.

        Demonstra a assimetria entre os dois monitores:
          _monitor_dia():         declara ultima_checagem_login = None ✓
          monitor_ticketmaster(): NÃO declara ultima_checagem_login_tm  ✗

        CONTRAEXEMPLO ESPERADO (código não corrigido):
          'ultima_checagem_login_tm' ausente do source de monitor_ticketmaster()
          enquanto 'ultima_checagem_login' está presente em _monitor_dia().

        FALHA ESPERADA: AssertionError para monitor_ticketmaster().
        """
        source_tm = inspect.getsource(bot.monitor_ticketmaster)
        source_dia = inspect.getsource(bot._monitor_dia)

        # _monitor_dia tem ultima_checagem_login (deve PASSAR — baseline)
        self.assertIn(
            'ultima_checagem_login',
            source_dia,
            msg=(
                "BASELINE QUEBRADA: '_monitor_dia()' não tem 'ultima_checagem_login'. "
                "Isto indica uma mudança inesperada no código de referência."
            ),
        )

        # monitor_ticketmaster DEVE ter ultima_checagem_login_tm (FALHA no não corrigido)
        self.assertIn(
            'ultima_checagem_login_tm',
            source_tm,
            msg=(
                "BUG CONFIRMADO (Req 1.2): monitor_ticketmaster() NÃO tem "
                "'ultima_checagem_login_tm', enquanto _monitor_dia() tem "
                "'ultima_checagem_login'.\n"
                "Contraexemplo: assimetria entre monitores — BuyTicket verifica "
                "sessão periodicamente, Ticketmaster nunca verifica."
            ),
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
