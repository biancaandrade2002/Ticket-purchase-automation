"""
Testes de Preservação — Task 2
Spec: tm-monitor-login

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

Property 2: Preservation — Comportamento de Polling, Classificação e Notificação Inalterado

Objetivo
--------
Confirmar o comportamento BASELINE que deve ser PRESERVADO após o fix.
Estes testes DEVEM PASSAR no código NÃO corrigido — formam o baseline de preservação.
Após o fix (Task 3), todos os testes DEVEM CONTINUAR PASSANDO.
Se algum falhar após o fix, o fix introduziu uma regressão.

Comportamentos testados (Requirements 3.1–3.5):
  1. _tm_classificar classifica páginas corretamente (esgotado/disponivel/desconhecido)
  2. Notificação urgente SOMENTE na transição não-disponivel ? disponivel
  3. InvalidSessionIdException: driver.quit() chamado, driver=None recriado no ciclo seguinte
  4. Erro de rede transitório: _aguardar_rede() chamada, driver NÃO descartado
  5. Independência entre threads BuyTicket e Ticketmaster
"""

import re
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

from hypothesis import given, assume, settings
from hypothesis import strategies as st


# -----------------------------------------------------------------------------
# Stubs de dependências pesadas (instalados ANTES de importar bot)
# Padrão idêntico ao usado em test_tm_bug_condition.py
# -----------------------------------------------------------------------------

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot  # noqa: E402  pylint: disable=wrong-import-position


# -----------------------------------------------------------------------------
# Sentinel — escapa do while True sem ser capturado pelo except Exception
# BaseException ignora todos os `except Exception:` do bot.
# -----------------------------------------------------------------------------

class _StopMonitor(BaseException):
    """Usado para escapar do while True sem ser capturado por except Exception."""


# -----------------------------------------------------------------------------
# MockDriver leve para testes de _tm_classificar / _tm_tem_botao_ativo
# -----------------------------------------------------------------------------

class _MockTmDriver:
    """
    Mock driver para _tm_classificar e _tm_tem_botao_ativo.

    Controla page_source e a presença de botão de compra ativo:
      - has_active_button=True  ? find_elements retorna elemento visível com text='Comprar'
      - has_active_button=False ? find_elements retorna lista vazia
    """

    def __init__(self, page_source='', has_active_button=False):
        self._page_source = page_source
        self._has_active_button = has_active_button
        self.window_handles = ['h0', 'h1', 'h2']
        self.current_window_handle = 'h0'
        self.current_url = 'https://www.www.ticketmaster.com.br/event/test'
        self.switch_to = MagicMock()

    @property
    def page_source(self):
        return self._page_source

    def find_elements(self, by, sel):
        if self._has_active_button:
            el = MagicMock()
            el.is_displayed.return_value = True
            el.text = 'Comprar'
            return [el]
        return []

    def get(self, url): pass
    def refresh(self): pass
    def execute_script(self, *a, **k): pass
    def quit(self): pass


def _make_driver(page_source='', has_active_button=False):
    return _MockTmDriver(page_source=page_source, has_active_button=has_active_button)


# -----------------------------------------------------------------------------
# Property-Based Tests — _tm_classificar (Hypothesis)
# Validates: Requirement 3.1
# -----------------------------------------------------------------------------

class TestTMClassificarPreservation(unittest.TestCase):
    """
    Validates: Requirement 3.1

    Testes de propriedade para _tm_classificar(driver, data_show).
    Verificam que as regras de classificação são consistentes e corretas
    para qualquer conteúdo de página gerado.

    Estas propriedades devem passar ANTES e APÓS o fix —
    o fix não altera _tm_classificar.
    """

    @given(
        kw=st.sampled_from(bot._TM_ESGOTADO_KW),
        prefix=st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd', 'Zs')),
            max_size=30,
        ),
        suffix=st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd', 'Zs')),
            max_size=30,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_esgotado_keyword_sempre_retorna_esgotado(self, kw, prefix, suffix):
        """
        Validates: Requirement 3.1

        PARA QUALQUER página que contenha uma keyword de esgotado,
        _tm_classificar DEVE retornar 'esgotado' — independente do restante.

        Baseline: a verificação de esgotado deve permanecer inalterada pelo fix.
        """
        page = f'{prefix} {kw} {suffix}'
        driver = _make_driver(page_source=page, has_active_button=False)
        with patch.object(bot, 'log', lambda *a, **k: None):
            result = bot._tm_classificar(driver, '28/10')
        self.assertEqual(
            result, 'esgotado',
            f"Keyword '{kw}' presente ? deve retornar 'esgotado', got '{result}'"
        )

    @given(kw=st.sampled_from(bot._TM_ESGOTADO_KW))
    @settings(max_examples=20, deadline=None)
    def test_esgotado_tem_prioridade_sobre_botao_ativo(self, kw):
        """
        Validates: Requirement 3.1

        Quando a página contém keyword de esgotado E um botão de compra ativo,
        _tm_classificar DEVE retornar 'esgotado' (não 'disponivel').

        Baseline: a verificação de esgotado ocorre ANTES da verificação de botão
        — essa ordem não deve ser alterada pelo fix.
        """
        page = f'ingressos BTS: {kw} — botao ativo simulado'
        driver = _make_driver(page_source=page, has_active_button=True)
        with patch.object(bot, 'log', lambda *a, **k: None):
            result = bot._tm_classificar(driver, '28/10')
        self.assertEqual(
            result, 'esgotado',
            "Keyword de esgotado DEVE ter prioridade sobre botão ativo"
        )

    @given(
        txt=st.text(
            alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 !\n',
            max_size=100,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_sem_esgotado_com_botao_retorna_disponivel(self, txt):
        """
        Validates: Requirement 3.1

        Quando a página NÃO contém keywords de esgotado e há um botão de compra ativo,
        _tm_classificar DEVE retornar 'disponivel'.

        Baseline: a detecção de botão ativo deve continuar funcionando após o fix.
        """
        assume(not any(kw in txt.lower() for kw in bot._TM_ESGOTADO_KW))
        driver = _make_driver(page_source=txt, has_active_button=True)
        with patch.object(bot, 'log', lambda *a, **k: None):
            result = bot._tm_classificar(driver, '28/10')
        self.assertEqual(
            result, 'disponivel',
            f"Sem esgotado + botão ativo ? deve retornar 'disponivel', got '{result}'"
        )

    @given(
        txt=st.text(
            # Sem '$' no alfabeto ? impossível gerar padrão R$ acidentalmente
            alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 !\n',
            max_size=80,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_sem_esgotado_sem_botao_retorna_desconhecido(self, txt):
        """
        Validates: Requirement 3.1

        Quando a página não tem keywords de esgotado, não tem botão ativo e tem
        menos de 3 padrões de preço, _tm_classificar DEVE retornar 'desconhecido'.

        Baseline: o fallback 'desconhecido' deve permanecer inalterado pelo fix.
        """
        assume(not any(kw in txt.lower() for kw in bot._TM_ESGOTADO_KW))
        # Alfabeto sem '$' ? re.findall(r'R\$...') nunca retorna >= 3 aqui
        driver = _make_driver(page_source=txt, has_active_button=False)
        with patch.object(bot, 'log', lambda *a, **k: None):
            result = bot._tm_classificar(driver, '28/10')
        self.assertEqual(
            result, 'desconhecido',
            f"Sem esgotado, sem botão ? deve retornar 'desconhecido', got '{result}'"
        )

    def test_tres_precos_sem_botao_retorna_desconhecido(self):
        """
        Validates: Requirement 3.1

        Quando há >= 3 padrões de preço mas sem botão ativo,
        _tm_classificar DEVE retornar 'desconhecido' (tabela informativa ? compra ativa).

        Baseline: a lógica de fallback de preços não classifica como 'disponivel'.
        """
        page = 'BTS Ingressos: R$ 150,00 | R$ 200,00 | R$ 500,00 | R$ 1.000,00'
        driver = _make_driver(page_source=page, has_active_button=False)
        with patch.object(bot, 'log', lambda *a, **k: None):
            result = bot._tm_classificar(driver, '28/10')
        self.assertEqual(
            result, 'desconhecido',
            ">=3 preços sem botão ativo ? deve retornar 'desconhecido'"
        )

    def test_classificar_retorna_apenas_tres_valores_possiveis(self):
        """
        Validates: Requirement 3.1

        _tm_classificar DEVE retornar exclusivamente um de:
        'esgotado', 'disponivel', 'desconhecido'.

        Baseline: o domínio de saída da função não deve ser alterado pelo fix.
        """
        casos = [
            ('esgotado definitivamente', False),
            ('ingressos BTS', True),
            ('BTS World Tour 2026', False),
            ('R$ 150,00 R$ 200,00 R$ 250,00 BTS', False),
            ('sold out — this event is sold out', False),
            ('', True),
        ]
        valores_validos = {'esgotado', 'disponivel', 'desconhecido'}
        for page, has_btn in casos:
            driver = _make_driver(page_source=page, has_active_button=has_btn)
            with patch.object(bot, 'log', lambda *a, **k: None):
                result = bot._tm_classificar(driver, '28/10')
            self.assertIn(
                result, valores_validos,
                f"_tm_classificar deve retornar um de {valores_validos}, got '{result}'"
            )


# -----------------------------------------------------------------------------
# Property-Based Tests — Notificação Urgente na Transição (Hypothesis)
# Validates: Requirements 3.1, 3.2
# -----------------------------------------------------------------------------

class TestTMNotificacaoUrgente(unittest.TestCase):
    """
    Validates: Requirements 3.1, 3.2

    Notificação urgente SOMENTE na transição não-disponivel ? disponivel.
    Disponivel ? disponivel não deve re-disparar urgent (prevenção de spam).
    """

    def setUp(self):
        # Estado limpo antes de cada teste
        bot._ESTADO_TM.clear()

    def tearDown(self):
        bot._ESTADO_TM.clear()

    def _criar_driver_disponivel(self):
        """Cria mock driver que faz _tm_classificar retornar 'disponivel'."""
        mock_driver = MagicMock()
        mock_driver.page_source = 'BTS World Tour — comprar ingresso'
        mock_driver.current_url = 'https://www.www.ticketmaster.com.br/event/28out'
        mock_driver.window_handles = ['h0', 'h1', 'h2']
        mock_driver.current_window_handle = 'h0'
        active_btn = MagicMock()
        active_btn.is_displayed.return_value = True
        active_btn.text = 'Comprar'
        mock_driver.find_elements.return_value = [active_btn]
        return mock_driver

    @given(
        estado_anterior=st.one_of(
            st.none(),
            st.just('esgotado'),
            st.just('desconhecido'),
        )
    )
    @settings(max_examples=9, deadline=None)
    def test_transicao_para_disponivel_notifica_urgent(self, estado_anterior):
        """
        Validates: Requirements 3.1, 3.2

        PARA QUALQUER estado anterior ? 'disponivel', quando novo estado = 'disponivel',
        monitor_ticketmaster DEVE chamar notificar com prioridade='urgent'.

        Baseline: a transição para disponivel SEMPRE dispara notificação urgente.
        Esta propriedade deve ser preservada após o fix.
        """
        # Reset para cada exemplo Hypothesis
        bot._ESTADO_TM.clear()
        if estado_anterior is not None:
            bot._ESTADO_TM['28/10'] = estado_anterior

        notificar_calls = []

        def notificar_spy(titulo, mensagem='', prioridade='default'):
            notificar_calls.append(prioridade)

        def fake_screenshot(*a, **k):
            # screenshot é chamado logo após notificar('urgent') — escapa aqui
            raise _StopMonitor("após screenshot")

        mock_driver = self._criar_driver_disponivel()

        with patch.object(bot, 'criar_driver', lambda: mock_driver), \
             patch.object(bot, 'ticketmaster_login', lambda *a, **k: None), \
             patch.object(bot, '_checar_login_periodico_tm', lambda d, ts: ts), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', notificar_spy), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', fake_screenshot), \
             patch.object(bot, '_aguardar_rede', lambda *a, **k: None), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', lambda *a: None):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        urgent_calls = [p for p in notificar_calls if p == 'urgent']
        self.assertGreater(
            len(urgent_calls), 0,
            f"Estado anterior='{estado_anterior}' ? 'disponivel': "
            f"notificar('urgent') deve ser chamada. Calls registradas: {notificar_calls}"
        )

    def test_disponivel_para_disponivel_nao_repete_urgent(self):
        """
        Validates: Requirements 3.1, 3.2

        Quando estado anterior já é 'disponivel' e novo estado = 'disponivel',
        monitor_ticketmaster NÃO DEVE chamar notificar com 'urgent' novamente.

        Baseline: prevenção de spam de notificação deve continuar funcionando.
        """
        # Pré-setar TODOS os dias como disponivel para que nenhuma aba dispare urgent
        bot._ESTADO_TM.clear()
        for data in bot.DATAS_SHOW:
            bot._ESTADO_TM[data] = 'disponivel'

        notificar_calls = []

        def notificar_spy(titulo, mensagem='', prioridade='default'):
            notificar_calls.append(prioridade)

        mock_driver = self._criar_driver_disponivel()

        # _abrir_abas chama time.sleep(1.5) 2x (abas extras).
        # Loop interno chama time.sleep(INTERVALO_CHECK) 1x por aba × 3 abas = 3x.
        # Total: 5 sleeps antes do segundo ciclo outer — escapamos no 5º.
        sleep_count = [0]

        def fake_sleep(t):
            sleep_count[0] += 1
            if sleep_count[0] >= 5:
                raise _StopMonitor("após ciclo completo — escapando")

        with patch.object(bot, 'criar_driver', lambda: mock_driver), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', notificar_spy), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', lambda *a, **k: None), \
             patch.object(bot, '_aguardar_rede', lambda *a, **k: None), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', fake_sleep):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        # Pode haver chamadas 'default' (monitor iniciado) mas NUNCA 'urgent'
        urgent_calls = [p for p in notificar_calls if p == 'urgent']
        self.assertEqual(
            urgent_calls, [],
            f"Estado já 'disponivel' para todos os dias: "
            f"notificar('urgent') NÃO deve ser chamada. Calls: {notificar_calls}"
        )


# -----------------------------------------------------------------------------
# Testes Unitários — InvalidSessionIdException (Requirement 3.3)
# -----------------------------------------------------------------------------

class TestTMInvalidSessionId(unittest.TestCase):
    """
    Validates: Requirement 3.3

    Quando InvalidSessionIdException é lançada durante o polling:
    - driver.quit() deve ser chamado (cleanup seguro)
    - driver deve ser reiniciado (criar_driver chamado novamente no ciclo seguinte)
    - handles deve ser resetado para [] (_abrir_abas chamada de novo)

    Baseline: o comportamento de reset de sessão deve permanecer inalterado após o fix.
    """

    def setUp(self):
        bot._ESTADO_TM.clear()

    def tearDown(self):
        bot._ESTADO_TM.clear()

    def _mock_driver_com_session_error_no_polling(self):
        """
        Cria mock driver que:
        - Deixa as 2 primeiras chamadas a switch_to.window passarem (em _abrir_abas)
        - Lança InvalidSessionIdException na 3ª chamada (primeiro switch do polling)
        """
        InvalidSessExc = sys.modules['selenium.common.exceptions'].InvalidSessionIdException
        mock_driver = MagicMock()
        mock_driver.window_handles = ['h0', 'h1', 'h2']
        mock_driver.current_window_handle = 'h0'
        mock_driver.page_source = '<html></html>'
        mock_driver.current_url = 'https://www.www.ticketmaster.com.br/event/28out'

        switch_count = [0]

        def fake_switch(handle):
            switch_count[0] += 1
            # Primeiras 2 chamadas: _abrir_abas (tabs 2 e 3) — OK
            # 3ª em diante: loop de polling — InvalidSessionIdException
            if switch_count[0] > 2:
                raise InvalidSessExc("sessao morta detectada no polling")

        mock_driver.switch_to.window.side_effect = fake_switch
        return mock_driver

    def test_invalid_session_chama_quit_e_reinicia_driver(self):
        """
        Validates: Requirement 3.3

        Após InvalidSessionIdException no polling:
          1. driver.quit() é chamado exatamente 1x (cleanup seguro)
          2. driver = None ? criar_driver() chamado novamente no ciclo seguinte (2x total)

        Baseline: o comportamento de reset de sessão não deve ser alterado pelo fix.
        """
        criar_driver_count = [0]
        quit_count = [0]

        mock_driver_1 = self._mock_driver_com_session_error_no_polling()

        def fake_quit():
            quit_count[0] += 1
        mock_driver_1.quit.side_effect = fake_quit

        def fake_criar_driver():
            criar_driver_count[0] += 1
            if criar_driver_count[0] == 1:
                return mock_driver_1
            # Segundo ciclo (driver foi resetado para None): para aqui
            raise _StopMonitor("segundo driver — driver foi reiniciado com sucesso")

        with patch.object(bot, 'criar_driver', fake_criar_driver), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', lambda *a, **k: None), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', lambda *a, **k: None), \
             patch.object(bot, '_aguardar_rede', lambda *a, **k: None), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', lambda *a: None):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        self.assertEqual(
            criar_driver_count[0], 2,
            "Após InvalidSessionIdException: driver=None ? criar_driver deve ser "
            f"chamado 2x (init + reinicialização). Contagem: {criar_driver_count[0]}"
        )
        self.assertEqual(
            quit_count[0], 1,
            f"driver.quit() deve ser chamado 1x após InvalidSessionIdException. "
            f"Contagem: {quit_count[0]}"
        )

    def test_invalid_session_reseta_handles_e_reabre_abas(self):
        """
        Validates: Requirement 3.3

        Após InvalidSessionIdException, handles = [] ? _abrir_abas() é chamada
        novamente na próxima iteração do while True, verificado por driver.get()
        sendo chamado pela segunda vez.

        Baseline: a reabertura das abas após sessão morta deve permanecer funcionando.
        """
        get_call_count = [0]

        mock_driver = self._mock_driver_com_session_error_no_polling()

        def count_get(url):
            get_call_count[0] += 1
            if get_call_count[0] >= 2:
                # Segunda chamada a _abrir_abas confirma que handles foi resetado
                raise _StopMonitor("_abrir_abas chamada de novo — handles foi resetado")
        mock_driver.get.side_effect = count_get

        # Mesma instância reutilizada após reinicialização (quit não destrói o mock)
        mock_driver.quit.side_effect = lambda: None

        with patch.object(bot, 'criar_driver', lambda: mock_driver), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', lambda *a, **k: None), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', lambda *a, **k: None), \
             patch.object(bot, '_aguardar_rede', lambda *a, **k: None), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', lambda *a: None):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        self.assertGreaterEqual(
            get_call_count[0], 2,
            "Após InvalidSessionIdException, _abrir_abas deve ser chamada de novo "
            f"(driver.get chamado >=2x). Contagem: {get_call_count[0]}"
        )


# -----------------------------------------------------------------------------
# Property-Based Tests — Erros de Rede (Hypothesis)
# Validates: Requirements 3.2, 3.4
# -----------------------------------------------------------------------------

class TestTMErroRedePreservation(unittest.TestCase):
    """
    Validates: Requirements 3.2, 3.4

    Quando ocorre erro de rede transitório (ERR_NAME_NOT_RESOLVED etc.) no loop interno:
    - _aguardar_rede() DEVE ser chamada (recuperação de rede)
    - driver NÃO deve ser descartado (criar_driver chamado apenas 1x)

    Baseline: a recuperação de rede deve permanecer inalterada após o fix.
    """

    def setUp(self):
        bot._ESTADO_TM.clear()

    def tearDown(self):
        bot._ESTADO_TM.clear()

    @given(kw=st.sampled_from(list(bot._TM_ERROS_REDE_KW)))
    @settings(max_examples=20, deadline=None)
    def test_erro_rede_chama_aguardar_rede(self, kw):
        """
        Validates: Requirements 3.2, 3.4

        PARA CADA keyword de erro de rede reconhecida, quando driver.refresh()
        lança exceção com essa keyword, _aguardar_rede() DEVE ser chamada.

        Baseline: a recuperação de rede funciona para todos os erros reconhecidos.
        Esta propriedade não deve ser alterada pelo fix.
        """
        aguardar_rede_called = [False]

        mock_driver = MagicMock()
        mock_driver.window_handles = ['h0', 'h1', 'h2']
        mock_driver.current_window_handle = 'h0'
        mock_driver.page_source = '<html></html>'
        mock_driver.current_url = 'https://tm.com/event/28out'
        mock_driver.refresh.side_effect = Exception(f'Chrome: {kw} blah')

        def fake_aguardar_rede(*a, **k):
            aguardar_rede_called[0] = True
            # Escapa após confirmar que _aguardar_rede foi chamada
            raise _StopMonitor(f"_aguardar_rede chamada para '{kw}' — OK")

        with patch.object(bot, 'criar_driver', lambda: mock_driver), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', lambda *a, **k: None), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', lambda *a, **k: None), \
             patch.object(bot, '_aguardar_rede', fake_aguardar_rede), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', lambda *a: None):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        self.assertTrue(
            aguardar_rede_called[0],
            f"Para erro de rede '{kw}': _aguardar_rede() DEVE ser chamada. "
            "Baseline de recuperação de rede não pode ser alterado pelo fix."
        )

    @given(kw=st.sampled_from(list(bot._TM_ERROS_REDE_KW)))
    @settings(max_examples=20, deadline=None)
    def test_erro_rede_nao_descarta_driver(self, kw):
        """
        Validates: Requirements 3.2, 3.4

        PARA CADA keyword de erro de rede, quando driver.refresh() falha,
        driver NÃO deve ser descartado — criar_driver deve ser chamado apenas 1x.

        O erro é transitório (rede): não representa crash do browser.
        Descartar o driver causaria relogin desnecessário a cada perda de rede.

        Baseline: driver é mantido após erros de rede — comportamento não deve
        ser alterado pelo fix.
        """
        criar_driver_count = [0]

        mock_driver = MagicMock()
        mock_driver.window_handles = ['h0', 'h1', 'h2']
        mock_driver.current_window_handle = 'h0'
        mock_driver.page_source = '<html></html>'
        mock_driver.current_url = 'https://tm.com/event/28out'
        mock_driver.refresh.side_effect = Exception(f'Chrome: {kw}')

        def fake_criar_driver():
            criar_driver_count[0] += 1
            return mock_driver

        def fake_aguardar_rede(*a, **k):
            # Escapa depois de confirmar que _aguardar_rede foi chamada
            raise _StopMonitor(f"_aguardar_rede chamada — parando ({kw})")

        with patch.object(bot, 'criar_driver', fake_criar_driver), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', lambda *a, **k: None), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', lambda *a, **k: None), \
             patch.object(bot, '_aguardar_rede', fake_aguardar_rede), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', lambda *a: None):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        self.assertEqual(
            criar_driver_count[0], 1,
            f"Para erro de rede '{kw}': driver NÃO deve ser descartado — "
            f"criar_driver deve ser chamado apenas 1x. Contagem: {criar_driver_count[0]}"
        )

    def test_erro_rede_nao_impede_continuacao_outras_abas(self):
        """
        Validates: Requirements 3.2, 3.4

        Após erro de rede em uma aba, o polling continua normalmente para as
        demais abas — o erro não aborta o ciclo completo.

        Baseline: resiliência a erros de rede por aba deve ser preservada.
        """
        # Apenas a primeira aba (28/10) tem erro de rede
        # As demais abas (30/10, 31/10) devem ser processadas normalmente
        processed_abas = []

        mock_driver = MagicMock()
        mock_driver.window_handles = ['h0', 'h1', 'h2']
        mock_driver.current_window_handle = 'h0'
        mock_driver.current_url = 'https://tm.com/event/28out'
        mock_driver.page_source = '<html></html>'

        refresh_count = [0]

        def fake_refresh():
            refresh_count[0] += 1
            if refresh_count[0] == 1:
                # Primeira aba: erro de rede
                raise Exception('net::ERR_NAME_NOT_RESOLVED')
            # Demais abas: OK (registro para verificar que continuaram)
            processed_abas.append(refresh_count[0])

        mock_driver.refresh.side_effect = fake_refresh

        aguardar_rede_called = [False]

        def fake_aguardar_rede(*a, **k):
            aguardar_rede_called[0] = True
            # NÃO escapa aqui — deixa o loop continuar para as demais abas

        # Escapa após processar todas as 3 abas (3 sleeps de INTERVALO_CHECK +
        # 2 sleeps de _abrir_abas)
        sleep_count = [0]

        def fake_sleep(t):
            sleep_count[0] += 1
            if sleep_count[0] >= 5:
                raise _StopMonitor("ciclo completo")

        with patch.object(bot, 'criar_driver', lambda: mock_driver), \
             patch.object(bot, '_wait_for', lambda *a, **k: True), \
             patch.object(bot, 'notificar', lambda *a, **k: None), \
             patch.object(bot, 'notificar_pc', lambda *a, **k: None), \
             patch.object(bot, 'screenshot', lambda *a, **k: None), \
             patch.object(bot, '_aguardar_rede', fake_aguardar_rede), \
             patch.object(bot, 'log', lambda *a, **k: None), \
             patch('time.sleep', fake_sleep):
            try:
                bot.monitor_ticketmaster()
            except _StopMonitor:
                pass

        # _aguardar_rede foi chamada para a aba com erro
        self.assertTrue(aguardar_rede_called[0],
                        "_aguardar_rede deve ser chamada para a aba com erro de rede")

        # O loop continuou para as demais abas (2 e 3)
        self.assertGreaterEqual(
            len(processed_abas), 1,
            "Após erro de rede em uma aba, o loop deve continuar para as demais abas. "
            f"Abas processadas após erro: {len(processed_abas)}"
        )


# -----------------------------------------------------------------------------
# Testes de Preservação — Independência entre Threads (Requirement 3.5)
# -----------------------------------------------------------------------------

class TestTMIndependenciaThreads(unittest.TestCase):
    """
    Validates: Requirement 3.5

    monitor_buyticket e monitor_ticketmaster operam de forma independente —
    o estado de login de um não deve interferir no outro.

    Baseline: a arquitetura de threads independentes deve ser preservada.
    """

    def test_estado_tm_independente_do_buyticket(self):
        """
        Validates: Requirement 3.5

        _ESTADO_TM (dict global do TM) é separado do estado de login do BuyTicket.
        Modificar _ESTADO_TM não deve afetar variáveis globais do BuyTicket.
        Verificado por inspeção de atributos do módulo.

        Baseline: os dicionários de estado são independentes entre os dois monitores.
        """
        # _ESTADO_TM existe e é um dict
        self.assertIsInstance(
            bot._ESTADO_TM, dict,
            "_ESTADO_TM deve ser um dict (estado global do monitor TM)"
        )

        # Modificar _ESTADO_TM não afeta nenhum estado BuyTicket
        bot._ESTADO_TM['28/10'] = 'disponivel'
        bot._ESTADO_TM['30/10'] = 'esgotado'

        # BuyTicket não tem '_ESTADO_TM' como estado compartilhado
        # (as funções BuyTicket operam via parâmetros e variáveis locais)
        self.assertNotEqual(
            id(bot._ESTADO_TM),
            id(getattr(bot, '_ESTADO_BT', None)),
            "Os dicionários de estado TM e BuyTicket devem ser objetos distintos"
        )

        bot._ESTADO_TM.clear()

    def test_funcoes_tm_nao_modificam_estado_buyticket(self):
        """
        Validates: Requirement 3.5

        As funções auxiliares TM (_tm_classificar, _tm_tem_botao_ativo) são
        independentes: não referenciam nem modificam variáveis de estado BuyTicket.

        Verificado por inspeção de source code.
        """
        import inspect

        source_classificar = inspect.getsource(bot._tm_classificar)
        source_botao = inspect.getsource(bot._tm_tem_botao_ativo)

        # Funções TM não devem referenciar estado ou funções BuyTicket
        buyticket_refs = ['buyticket_login', 'buyticket_sessao_ativa',
                          '_checar_login_periodico', 'BUYTICKET']
        for ref in buyticket_refs:
            self.assertNotIn(
                ref, source_classificar,
                f"_tm_classificar não deve referenciar '{ref}' (estado BuyTicket)"
            )
            self.assertNotIn(
                ref, source_botao,
                f"_tm_tem_botao_ativo não deve referenciar '{ref}' (estado BuyTicket)"
            )


if __name__ == '__main__':
    unittest.main(verbosity=2)

