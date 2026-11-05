"""
Testes de integração do fluxo de seleção resiliente em bot.py.

O bot.py importa Selenium/requests/dotenv/webdriver_manager no topo. Esses
pacotes não são necessários para testar a lógica de orquestração, então
injetamos stubs leves em sys.modules ANTES de importar bot. Um "driver" e
"WebElements" falsos simulam o DOM do BuyTicket (dropdowns, itens, botão de
compra), permitindo verificar:

- seleção confirmada no DOM (Task 4);
- retry por etapa com clique que "não registra" (Task 4);
- validação final estrita: compra quando o valor bate (Task 8);
- divergência recarrega e refaz, e sucesso após reload (Task 7/8);
- esgotamento de ciclos pula para a próxima combinação (Task 8);
- priorização por setor no fluxo completo (Task 5/8).
"""

import sys
import types


# ------------------------------------------------------------
# Stubs dos módulos pesados (instalados em sys.modules antes de importar bot)
# ------------------------------------------------------------

def _instalar_stubs():
    # requests
    requests_stub = types.ModuleType('requests')
    requests_stub.post = lambda *a, **k: None
    sys.modules.setdefault('requests', requests_stub)

    # dotenv
    dotenv_stub = types.ModuleType('dotenv')
    dotenv_stub.load_dotenv = lambda *a, **k: None
    sys.modules.setdefault('dotenv', dotenv_stub)

    # selenium e submódulos
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
        def __init__(self, driver, timeout, *a, **k):
            self._driver = driver
            self._timeout = timeout

        def until(self, condition, *a, **k):
            # Tenta avaliar a condição com o driver; se lançar ou retornar falsy → None
            try:
                result = condition(self._driver)
                return result if result else None
            except Exception:
                return None
    ui_mod.WebDriverWait = WebDriverWait
    ec_mod = types.ModuleType('selenium.webdriver.support.expected_conditions')
    ec_mod.presence_of_element_located = lambda *a, **k: (lambda d: True)

    def _vis_located(locator):
        """Retorna o primeiro elemento visível ou None."""
        def _check(driver):
            try:
                by, sel = locator
                els = driver.find_elements(by, sel)
                for e in els:
                    try:
                        if e.is_displayed():
                            return e
                    except Exception:
                        return e  # sem is_displayed → assume visível
                return None
            except Exception:
                return None
        return _check

    def _invis_located(locator):
        """Retorna True se o elemento não for encontrado ou não for visível."""
        def _check(driver):
            try:
                by, sel = locator
                els = driver.find_elements(by, sel)
                if not els:
                    return True
                for e in els:
                    try:
                        if e.is_displayed():
                            return False
                    except Exception:
                        pass
                return True
            except Exception:
                return True
        return _check

    def _url_changes(original_url):
        return lambda d: (d.current_url or '') != original_url

    ec_mod.visibility_of_element_located = _vis_located
    ec_mod.invisibility_of_element_located = _invis_located
    ec_mod.url_changes = _url_changes

    # selenium.common.exceptions — necessário para o novo bot.py
    class _FakeException(Exception):
        pass
    exc_mod = types.ModuleType('selenium.common.exceptions')
    exc_mod.InvalidSessionIdException = _FakeException
    exc_mod.TimeoutException = _FakeException
    exc_mod.NoSuchElementException = _FakeException

    # selenium.common (pacote pai de exceptions)
    sel_common = types.ModuleType('selenium.common')
    sel_common.exceptions = exc_mod

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
    sys.modules['selenium.common'] = sel_common
    sys.modules['selenium.common.exceptions'] = exc_mod

    # webdriver_manager
    wdm = types.ModuleType('webdriver_manager')
    wdm_chrome = types.ModuleType('webdriver_manager.chrome')

    class ChromeDriverManager:
        def install(self):
            return 'fake-driver'
    wdm_chrome.ChromeDriverManager = ChromeDriverManager
    sys.modules['webdriver_manager'] = wdm
    sys.modules['webdriver_manager.chrome'] = wdm_chrome


_instalar_stubs()

import pytest  # noqa: E402
import bot  # noqa: E402


# ------------------------------------------------------------
# DOM falso: WebElements e Driver
# ------------------------------------------------------------

class FakeEl:
    """Elemento simples com texto, atributos e filhos por seletor CSS."""

    def __init__(self, text='', attrs=None, children=None, on_click=None):
        self.text = text
        self._attrs = attrs or {}
        self._children = children or {}
        self._on_click = on_click

    def get_attribute(self, name):
        return self._attrs.get(name)

    def is_displayed(self):
        return self._attrs.get('_hidden', False) is False

    def click(self):
        if self._on_click:
            self._on_click(self)

    def find_elements(self, by, selector):
        return self._children.get(selector, [])

    def find_element(self, by, selector):
        els = self._children.get(selector, [])
        if not els:
            raise Exception('no element')
        return els[0]


class FakeDriver:
    """
    Driver que modela dois dropdowns (Tipo e Categoria) e um botão de compra.

    Config via cenário:
      - tipos: lista de (nome, preco_min)
      - categorias: lista de (nome, preco) exibidas após selecionar o tipo
      - preco_botao_por_selecao: função(sel_tipo, sel_cat) -> int|None
      - clicks_que_falham: conjunto de nomes cujo 1º clique "não registra"
    """

    def __init__(self, tipos, categorias, preco_botao_fn,
                 clicks_que_falham=None, url_atual='https://buyticketbrasil.com/evento/x'):
        self.tipos = tipos
        self.categorias = categorias
        self.preco_botao_fn = preco_botao_fn
        self.clicks_que_falham = set(clicks_que_falham or [])
        self._ja_clicou = set()
        self.current_url = url_atual
        self.sel_tipo = None
        self.sel_cat = None
        self.dropdown_aberto = None  # 0, 1 ou None
        self.reloads = 0
        self.comprou = False

    # --- API usada pelo bot ---
    def implicitly_wait(self, *a, **k):
        pass

    def execute_cdp_cmd(self, *a, **k):
        pass

    def execute_script(self, script, *args):
        # window.open: apenas registra a abertura; não abre aba real no fake
        return None

    def find_elements(self, by, selector):
        if selector == 'button[class*="dropdownHead"]':
            return [self._head(0), self._head(1)]
        if selector == 'button[class*="dropdownItem"]':
            return self._itens_do_dropdown_aberto()
        if selector == 'button[class*="dropdownItem"][aria-selected="true"]':
            return self._itens_selecionados()
        if selector == 'a[class*="buyBtn"]':
            return [self._buy_btn()]
        # ul[role="listbox"] — confirma que o dropdown está aberto
        if selector == 'ul[role="listbox"]':
            if self.dropdown_aberto is not None:
                return [FakeEl(text='listbox')]
            return []
        # Qualquer outro seletor retorna lista vazia (não afeta o fluxo testado)
        return []

    def find_element(self, by, selector):
        els = self.find_elements(by, selector)
        if not els:
            raise Exception('no element: ' + selector)
        return els[0]

    def get(self, url):
        self.reloads += 1
        self.current_url = url
        self._reset_selecao()

    def refresh(self):
        self.reloads += 1
        self._reset_selecao()

    # --- helpers internos ---
    def _reset_selecao(self):
        self.sel_tipo = None
        self.sel_cat = None
        self.dropdown_aberto = None
        self._ja_clicou = set()

    def _head(self, indice):
        expandido = 'true' if self.dropdown_aberto == indice else 'false'
        if indice == 0:
            texto = self.sel_tipo or 'Selecione o tipo'
        else:
            texto = self.sel_cat or 'Selecione a categoria'

        def abrir(_el, idx=indice):
            # Alterna abertura do dropdown.
            self.dropdown_aberto = None if self.dropdown_aberto == idx else idx
        return FakeEl(text=texto, attrs={'aria-expanded': expandido}, on_click=abrir)

    def _preco_badge(self, preco):
        if preco is None:
            return FakeEl(text='')
        # Formata como o site real: separador de milhar com ponto (R$ 1.000).
        return FakeEl(text='R$ ' + f'{preco:,}'.replace(',', '.'))

    def _fazer_item(self, nome, preco, indice):
        selecionado = (
            (indice == 0 and self.sel_tipo == nome) or
            (indice == 1 and self.sel_cat == nome)
        )

        def clicar(_el, nome=nome, idx=indice):
            # Simula clique que "não registra" na primeira vez para nomes marcados.
            chave = (idx, nome)
            if nome in self.clicks_que_falham and chave not in self._ja_clicou:
                self._ja_clicou.add(chave)
                return  # clique fantasma: não seleciona
            if idx == 0:
                self.sel_tipo = nome
                self.sel_cat = None  # trocar tipo reinicia categoria
            else:
                self.sel_cat = nome
            self.dropdown_aberto = None

        children = {
            'span[class*="itemName"]': [FakeEl(text=nome)],
            'span[class*="priceBadge"]': [self._preco_badge(preco)],
        }
        attrs = {'aria-selected': 'true' if selecionado else 'false'}
        return FakeEl(text=nome, attrs=attrs, children=children, on_click=clicar)

    def _itens_do_dropdown_aberto(self):
        if self.dropdown_aberto == 0:
            return [self._fazer_item(n, p, 0) for n, p in self.tipos]
        if self.dropdown_aberto == 1:
            return [self._fazer_item(n, p, 1) for n, p in self.categorias]
        return []

    def _itens_selecionados(self):
        itens = []
        if self.sel_tipo:
            itens.append(self._fazer_item(self.sel_tipo, 0, 0))
        if self.sel_cat:
            itens.append(self._fazer_item(self.sel_cat, 0, 1))
        return itens

    def _buy_btn(self):
        preco = self.preco_botao_fn(self.sel_tipo, self.sel_cat)
        texto = f'Comprar R$ {preco}' if preco is not None else 'Comprar'

        def comprar(_el):
            self.comprou = True
        return FakeEl(text=texto, on_click=comprar)


@pytest.fixture(autouse=True)
def _sem_sleep_e_sem_efeitos(monkeypatch):
    """Neutraliza sleeps, notificações, DRY_RUN e checkout para os testes."""
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(bot, 'notificar', lambda *a, **k: None)
    monkeypatch.setattr(bot, 'buyticket_login', lambda *a, **k: None)
    monkeypatch.setattr(bot, 'screenshot', lambda *a, **k: None)
    # DRY_RUN desativado para que buyticket_efetuar_compra chegue até o checkout.
    monkeypatch.setattr(bot, 'DRY_RUN', False)
    # _wait_for_any: retorna imediatamente None (sem polling real nos testes).
    monkeypatch.setattr(bot, '_wait_for_any', lambda driver, selectors, timeout=8: None)
    # _wait_for: retorna imediatamente um objeto truthy para não bloquear fluxo.
    _orig_wait_for = bot._wait_for
    def _fast_wait_for(driver, selector, timeout=8, by=None):
        try:
            els = driver.find_elements(by or 'css', selector)
            return els[0] if els else None
        except Exception:
            return None
    monkeypatch.setattr(bot, '_wait_for', _fast_wait_for)
    # _wait_until_gone: retorna True imediatamente (overlay não existe no fake).
    monkeypatch.setattr(bot, '_wait_until_gone', lambda *a, **k: True)
    # _wait_for_url_change: retorna True imediatamente.
    monkeypatch.setattr(bot, '_wait_for_url_change', lambda *a, **k: True)
    # checkout apenas sinaliza sucesso.
    monkeypatch.setattr(bot, 'buyticket_checkout',
                        lambda driver, *a, **k: True)


# ------------------------------------------------------------
# Testes
# ------------------------------------------------------------

def test_selecao_confirmada_primeira_tentativa():
    driver = FakeDriver(
        tipos=[('Pista', 900)],
        categorias=[('Meia', 1000)],
        preco_botao_fn=lambda t, c: 900,
    )
    assert bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_TIPO, 'Pista') is True
    assert driver.sel_tipo == 'Pista'


def test_selecao_confirma_apos_clique_fantasma():
    # Primeiro clique em 'Pista' não registra; retry confirma.
    driver = FakeDriver(
        tipos=[('Pista', 900)],
        categorias=[('Meia', 1000)],
        preco_botao_fn=lambda t, c: 900,
        clicks_que_falham={'Pista'},
    )
    assert bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_TIPO, 'Pista') is True
    assert driver.sel_tipo == 'Pista'


def test_selecao_esgota_tentativas_quando_item_ausente():
    driver = FakeDriver(
        tipos=[('Arquibancada', 400)],
        categorias=[],
        preco_botao_fn=lambda t, c: 400,
    )
    assert bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_TIPO, 'Pista') is False


def test_fluxo_compra_sucesso_primeiro_ciclo():
    # Botão reflete o preço da categoria selecionada; valida e compra.
    def preco_botao(t, c):
        if t == 'Pista' and c == 'Meia':
            return 1000
        return 900  # preço inicial (menor ingresso)
    driver = FakeDriver(
        tipos=[('Pista', 900)],
        categorias=[('Meia', 1000)],
        preco_botao_fn=preco_botao,
    )
    ok = bot.buyticket_fluxo_compra(driver, '28/10', 'https://buyticketbrasil.com/evento/x')
    assert ok is True
    assert driver.comprou is True


def test_fluxo_prioriza_pista_sobre_arquibancada():
    # Ambos válidos; Pista deve ser tentada e comprada primeiro.
    def preco_botao(t, c):
        if t == 'Pista' and c == 'Inteira':
            return 1500
        return 400
    driver = FakeDriver(
        tipos=[('Arquibancada', 400), ('Pista', 1500)],
        categorias=[('Inteira', 1500)],
        preco_botao_fn=preco_botao,
    )
    ok = bot.buyticket_fluxo_compra(driver, '28/10', 'https://buyticketbrasil.com/evento/x')
    assert ok is True
    assert driver.sel_tipo == 'Pista'


def test_divergencia_recarrega_e_refaz_com_sucesso():
    # 1º ciclo: botão diverge (999 != 1000). Após recarregar, bate (1000) e compra.
    # O fluxo recarrega via driver.refresh() (mantendo a página do dia).
    estado = {'reloaded': False}

    def preco_botao(t, c):
        if t == 'Pista' and c == 'Meia':
            return 1000 if estado['reloaded'] else 999
        return 900
    driver = FakeDriver(
        tipos=[('Pista', 900)],
        categorias=[('Meia', 1000)],
        preco_botao_fn=preco_botao,
    )
    # Marca reloaded quando o driver recarrega (refresh mantém a página do dia).
    original_refresh = driver.refresh

    def refresh():
        estado['reloaded'] = True
        original_refresh()
    driver.refresh = refresh

    ok = bot.buyticket_fluxo_compra(driver, '28/10', 'https://buyticketbrasil.com/evento/x')
    assert ok is True
    assert driver.comprou is True
    assert driver.reloads >= 1


def test_divergencia_persistente_esgota_ciclos():
    # Botão sempre diverge -> esgota 3 ciclos -> não compra.
    def preco_botao(t, c):
        if t == 'Pista' and c == 'Meia':
            return 999  # nunca bate com 1000
        return 900
    driver = FakeDriver(
        tipos=[('Pista', 900)],
        categorias=[('Meia', 1000)],
        preco_botao_fn=preco_botao,
    )
    ok = bot.buyticket_fluxo_compra(driver, '28/10', 'https://buyticketbrasil.com/evento/x')
    assert ok is False
    assert driver.comprou is False
    # MAX_CICLOS - 1 reloads (o último ciclo não recarrega).
    assert driver.reloads == bot.MAX_CICLOS - 1


def test_botao_inicial_fora_parametro_nao_prossegue():
    driver = FakeDriver(
        tipos=[('Pista', 900)],
        categorias=[('Meia', 1000)],
        preco_botao_fn=lambda t, c: 99999,  # acima do maior limite
    )
    ok = bot.buyticket_fluxo_compra(driver, '28/10', 'https://buyticketbrasil.com/evento/x')
    assert ok is False
    assert driver.comprou is False


# ============================================================
# Driver fake para navegação multi-aba (sessão, abas por dia, refresh)
# ============================================================

class FakeTabDriver:
    """
    Driver fake focado em navegação: múltiplas abas, troca de aba, get/refresh
    e presença (ou não) do formulário de login.

    - `url_por_visita(url)` decide qual URL fica ativa após um `get` (permite
      simular redirect para /entrar quando a sessão caiu).
    - `tem_form_login` indica se o input de email aparece na página atual.
    """

    def __init__(self, url_por_visita=None, tem_form_login=False,
                 url_inicial='https://buyticketbrasil.com/'):
        self._contador = 0
        self._handles = ['w0']
        self._atual = 'w0'
        self.url_por_visita = url_por_visita or (lambda u: u)
        self.tem_form_login = tem_form_login
        self.current_url = url_inicial
        self.gets = []       # histórico de get(url)
        self.refreshes = 0
        self.closed = []     # handles fechados

    # --- navegação ---
    def implicitly_wait(self, *a, **k):
        pass

    def execute_cdp_cmd(self, *a, **k):
        pass

    @property
    def page_source(self):
        return '<html></html>'

    def get(self, url):
        self.gets.append(url)
        self.current_url = self.url_por_visita(url)

    def refresh(self):
        self.refreshes += 1
        # Mantém current_url inalterada (simula refresh sem sair da página)

    # --- abas ---
    @property
    def current_window_handle(self):
        return self._atual

    @property
    def window_handles(self):
        return list(self._handles)

    def execute_script(self, script):
        if 'window.open' in script:
            self._contador += 1
            novo = f'w{self._contador}_open'
            self._handles.append(novo)

    class _SwitchTo:
        def __init__(self, driver):
            self._d = driver

        def window(self, handle):
            self._d._atual = handle

    @property
    def switch_to(self):
        return FakeTabDriver._SwitchTo(self)

    def close(self):
        self.closed.append(self._atual)
        if self._atual in self._handles:
            self._handles.remove(self._atual)

    # --- DOM mínimo (form de login) ---
    def find_elements(self, by, selector):
        if selector == 'input[placeholder="Seu email"]' and self.tem_form_login:
            return [object()]
        return []


# ------------------------------------------------------------
# Task 2 - buyticket_sessao_ativa
# ------------------------------------------------------------

def test_sessao_ativa_quando_nao_redireciona(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    # get mantém a URL de conta (sem 'entrar') e sem form de login => ativa.
    driver = FakeTabDriver(tem_form_login=False)
    assert bot.buyticket_sessao_ativa(driver) is True
    # A aba auxiliar deve ter sido fechada e o handle restaurado.
    assert driver.current_window_handle == 'w0'


def test_sessao_caiu_quando_redireciona_para_entrar(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    # Qualquer get resolve para a página de login.
    driver = FakeTabDriver(
        url_por_visita=lambda u: 'https://buyticketbrasil.com/entrar',
    )
    assert bot.buyticket_sessao_ativa(driver) is False
    assert driver.current_window_handle == 'w0'


def test_sessao_caiu_quando_form_login_presente(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    # URL não tem 'entrar', mas o formulário de login está presente.
    driver = FakeTabDriver(
        url_por_visita=lambda u: 'https://buyticketbrasil.com/minha-conta',
        tem_form_login=True,
    )
    assert bot.buyticket_sessao_ativa(driver) is False


# ------------------------------------------------------------
# Task 3 - abrir_abas_por_dia
# ------------------------------------------------------------

def test_abrir_abas_por_dia_cria_uma_aba_por_dia(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    driver = FakeTabDriver()
    urls = ['http://a?data=1', 'http://b?data=2', 'http://c?data=3']
    datas = ['28/10', '30/10', '31/10']

    abas = bot.abrir_abas_por_dia(driver, urls, datas)

    assert set(abas.keys()) == {'28/10', '30/10', '31/10'}
    # Handles distintos (primeira reaproveita a aba atual w0).
    handles = [abas[d]['handle'] for d in datas]
    assert len(set(handles)) == 3
    assert handles[0] == 'w0'
    # Cada aba recebeu sua URL do dia.
    assert abas['28/10']['url'] == 'http://a?data=1'
    assert abas['30/10']['url'] == 'http://b?data=2'
    assert abas['31/10']['url'] == 'http://c?data=3'
    # Todas as URLs foram navegadas.
    assert driver.gets == urls


# ------------------------------------------------------------
# Task 4 - buyticket_refresh_dia / _refresh_mantendo_dia
# ------------------------------------------------------------

def test_refresh_dia_usa_refresh_quando_mantem_pagina(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    url_dia = 'https://buyticketbrasil.com/evento/x?data=123&c=SP'
    # refresh mantém a página do dia (mesmo data=123).
    driver = FakeTabDriver(url_inicial=url_dia)
    aba = {'handle': 'w0', 'url': url_dia}

    bot.buyticket_refresh_dia(driver, aba)

    assert driver.refreshes == 1
    assert driver.gets == []  # não precisou de fallback


def test_refresh_dia_fallback_get_quando_sai_da_pagina(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    url_dia = 'https://buyticketbrasil.com/evento/x?data=123&c=SP'
    # Após refresh a URL "cai" para o menu inicial (sem data).
    driver = FakeTabDriver(url_inicial='https://buyticketbrasil.com/')
    aba = {'handle': 'w0', 'url': url_dia}

    bot.buyticket_refresh_dia(driver, aba)

    assert driver.refreshes == 1
    assert driver.gets == [url_dia]  # fallback navegou para a URL do dia


# ------------------------------------------------------------
# Task 5 - checagem periódica de login no monitor
# ------------------------------------------------------------

def test_checar_login_periodico_reloga_apos_intervalo(monkeypatch):
    chamou = {'login': 0, 'sessao': 0}
    monkeypatch.setattr(bot, 'buyticket_login', lambda d: chamou.__setitem__('login', chamou['login'] + 1))

    def sessao(_d):
        chamou['sessao'] += 1
        return False  # sessão caiu
    monkeypatch.setattr(bot, 'buyticket_sessao_ativa', sessao)
    monkeypatch.setattr(bot, 'notificar', lambda *a, **k: None)
    # Força a decisão temporal: sempre "deve checar".
    monkeypatch.setattr(bot, 'deve_checar_login', lambda *a, **k: True)

    novo = bot._checar_login_periodico(driver=object(), ultima_checagem_login=None)

    assert chamou['sessao'] == 1
    assert chamou['login'] == 1  # re-logou porque a sessão caiu
    assert isinstance(novo, float)  # timestamp atualizado


def test_checar_login_periodico_nao_checa_antes_do_intervalo(monkeypatch):
    chamou = {'sessao': 0}
    monkeypatch.setattr(bot, 'buyticket_sessao_ativa',
                        lambda d: chamou.__setitem__('sessao', chamou['sessao'] + 1) or True)
    # Ainda não é hora de checar.
    monkeypatch.setattr(bot, 'deve_checar_login', lambda *a, **k: False)

    anterior = 123.0
    novo = bot._checar_login_periodico(driver=object(), ultima_checagem_login=anterior)

    assert chamou['sessao'] == 0   # não checou a sessão
    assert novo == anterior        # timestamp inalterado
