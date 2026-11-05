"""
Testes de Regras de Negócio — BuyTicket Bot

Cobre as regras de negócio que governam o comportamento do bot:
- Mapeamento de setores e categorias (incluindo novos tipos do site)
- DRY_RUN bloqueia compra real e retorna False
- Prioridade de setor: Pista > Cadeira Inferior > Cadeira Superior > Arquibancada
- Limites por combinação setor x tipo: inteira e meia são independentes
- Fluxo abandona setor sem categoria válida e tenta próximo
- Múltiplas categorias válidas: escolhe a de MENOR preço
- Fila de espera: bot aguarda antes de selecionar
- Modal de segurança "Combinado" deve ser fechado antes de avançar
- Validação final: divergência de valor bloqueia compra
"""

import sys
import types
from unittest.mock import MagicMock, patch, PropertyMock

# ── Stubs dos módulos pesados ─────────────────────────────────────────────────

def _instalar_stubs():
    requests_stub = types.ModuleType('requests')
    requests_stub.post = lambda *a, **k: None
    sys.modules.setdefault('requests', requests_stub)

    dotenv_stub = types.ModuleType('dotenv')
    dotenv_stub.load_dotenv = lambda *a, **k: None
    sys.modules.setdefault('dotenv', dotenv_stub)

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
        def __init__(self, *a, **k):
            pass
        def until(self, *a, **k):
            return True
    ui_mod.WebDriverWait = WebDriverWait
    ec_mod = types.ModuleType('selenium.webdriver.support.expected_conditions')
    ec_mod.presence_of_element_located = lambda *a, **k: (lambda d: True)

    for mod, name in [
        (selenium_stub,  'selenium'),
        (webdriver_stub, 'selenium.webdriver'),
        (common,         'selenium.webdriver.common'),
        (by_mod,         'selenium.webdriver.common.by'),
        (keys_mod,       'selenium.webdriver.common.keys'),
        (chrome,         'selenium.webdriver.chrome'),
        (service_mod,    'selenium.webdriver.chrome.service'),
        (options_mod,    'selenium.webdriver.chrome.options'),
        (support,        'selenium.webdriver.support'),
        (ui_mod,         'selenium.webdriver.support.ui'),
        (ec_mod,         'selenium.webdriver.support.expected_conditions'),
    ]:
        sys.modules[name] = mod

    wdm = types.ModuleType('webdriver_manager')
    wdm_chrome = types.ModuleType('webdriver_manager.chrome')

    class ChromeDriverManager:
        def install(self):
            return 'fake-driver'
    wdm_chrome.ChromeDriverManager = ChromeDriverManager
    sys.modules['webdriver_manager'] = wdm
    sys.modules['webdriver_manager.chrome'] = wdm_chrome


_instalar_stubs()

import pytest               # noqa: E402
import bot                  # noqa: E402
import ticket_logic as tl   # noqa: E402


@pytest.fixture(autouse=True)
def _neutraliza_efeitos(monkeypatch):
    monkeypatch.setattr(bot.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(bot, 'notificar', lambda *a, **k: None)
    monkeypatch.setattr(bot, 'screenshot', lambda *a, **k: None)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MAPEAMENTO DE SETORES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMapeamentoSetores:

    def test_todos_camarotes_excluidos(self):
        setores = [
            {'nome': 'Camarote Stadium',           'preco_min': 100},
            {'nome': 'Camarote Ídolos',            'preco_min': 100},
            {'nome': 'Camarote Oitta',             'preco_min': 100},
            {'nome': 'Camarote Raça',              'preco_min': 100},
            {'nome': 'Camarote Experience + Pista','preco_min': 100},
            {'nome': 'Camarote Clássico',          'preco_min': 100},
        ]
        assert tl.filtrar_setores_validos(setores) == []

    def test_todos_setores_validos_reconhecidos(self):
        setores = [
            {'nome': 'Pista',            'preco_min': 100},
            {'nome': 'Cadeira Inferior', 'preco_min': 100},
            {'nome': 'Cadeira Superior', 'preco_min': 100},
            {'nome': 'Arquibancada',     'preco_min': 100},
        ]
        validos = tl.filtrar_setores_validos(setores)
        keys = {s['key'] for s in validos}
        assert keys == {'pista', 'cadeira_inferior', 'cadeira_superior', 'arquibancada'}

    def test_mapeamento_case_insensitive(self):
        assert tl.mapear_setor('PISTA') == 'pista'
        assert tl.mapear_setor('Cadeira Superior') == 'cadeira_superior'
        assert tl.mapear_setor('CADEIRA INFERIOR') == 'cadeira_inferior'
        assert tl.mapear_setor('arquibancada') == 'arquibancada'

    def test_setor_desconhecido_retorna_none(self):
        assert tl.mapear_setor('Golden Gate') is None
        assert tl.mapear_setor('') is None
        assert tl.mapear_setor(None) is None

    def test_filtro_preserva_campos_extras(self):
        """Campos extras do dict (ex: handle do WebElement) não devem ser perdidos."""
        setores = [{'nome': 'Pista', 'preco_min': 500, 'elemento': 'el_123'}]
        validos = tl.filtrar_setores_validos(setores)
        assert validos[0].get('elemento') == 'el_123'


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MAPEAMENTO DE CATEGORIAS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMapeamentoCategorias:

    def test_inteira_mapeada(self):
        assert tl.mapear_categoria('Inteira') == 'inteira'
        assert tl.mapear_categoria('inteira') == 'inteira'

    def test_meia_estudante_mapeada_como_meia(self):
        assert tl.mapear_categoria('Meia Estudante') == 'meia'

    def test_meia_idoso_mapeada_como_meia(self):
        assert tl.mapear_categoria('Meia Idoso') == 'meia'

    def test_meia_generica_mapeada(self):
        assert tl.mapear_categoria('Meia') == 'meia'

    @pytest.mark.parametrize('nome', [
        'Meia PCD',
        'Meia Jovem Baixa Renda',
        'Meia Professor',
        'Meia aposentado',
        'Acompanhante PCD',
        'Camarote',
    ])
    def test_categorias_nao_mapeadas_retornam_none(self, nome):
        assert tl.mapear_categoria(nome) is None, f"'{nome}' deveria ser None"

    def test_filtrar_categorias_exclui_nao_mapeadas(self):
        categorias = [
            {'nome': 'Meia PCD',              'preco': 100},
            {'nome': 'Meia Jovem Baixa Renda','preco': 100},
            {'nome': 'Inteira',               'preco': 500},
        ]
        validas = tl.filtrar_categorias_validas(categorias, 'arquibancada')
        nomes = [c['nome'] for c in validas]
        assert 'Meia PCD' not in nomes
        assert 'Meia Jovem Baixa Renda' not in nomes
        assert 'Inteira' in nomes

    def test_categoria_none_ou_vazia_retorna_none(self):
        assert tl.mapear_categoria(None) is None
        assert tl.mapear_categoria('') is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LIMITES POR SETOR E TIPO
# ═══════════════════════════════════════════════════════════════════════════════

class TestLimitesPreco:

    @pytest.mark.parametrize('setor,tipo_nome,preco,esperado', [
        # Arquibancada
        ('arquibancada', 'Inteira', 600,  True),
        ('arquibancada', 'Inteira', 601,  False),
        ('arquibancada', 'Meia',    500,  True),
        ('arquibancada', 'Meia',    501,  False),
        # Cadeira Superior
        ('cadeira_superior', 'Inteira', 900,  True),
        ('cadeira_superior', 'Inteira', 901,  False),
        ('cadeira_superior', 'Meia',    600,  True),
        ('cadeira_superior', 'Meia',    601,  False),
        # Cadeira Inferior
        ('cadeira_inferior', 'Inteira', 1100, True),
        ('cadeira_inferior', 'Inteira', 1101, False),
        ('cadeira_inferior', 'Meia',    700,  True),
        ('cadeira_inferior', 'Meia',    701,  False),
        # Pista
        ('pista', 'Inteira', 4500, True),
        ('pista', 'Inteira', 4501, False),
        ('pista', 'Meia',    1000, True),
        ('pista', 'Meia',    1001, False),
    ])
    def test_limite_exato_por_setor_e_tipo(self, setor, tipo_nome, preco, esperado):
        categorias = [{'nome': tipo_nome, 'preco': preco}]
        validas = tl.filtrar_categorias_validas(categorias, setor)
        assert bool(validas) == esperado, (
            f"{setor}/{tipo_nome} R${preco}: "
            f"esperado {'aceito' if esperado else 'rejeitado'}"
        )

    def test_pista_inteira_aceita_onde_pista_meia_rejeitada(self):
        """R$1.500 cabe em Pista Inteira (lim 4100) mas não em Pista Meia (lim 1000)."""
        assert tl.filtrar_categorias_validas([{'nome': 'Inteira', 'preco': 1500}], 'pista') != []
        assert tl.filtrar_categorias_validas([{'nome': 'Meia',    'preco': 1500}], 'pista') == []

    def test_menor_preco_entre_categorias_validas(self):
        """O fluxo escolhe a categoria de menor preço entre as válidas."""
        categorias = [
            {'nome': 'Inteira',       'preco': 900},
            {'nome': 'Meia Estudante','preco': 800},
            {'nome': 'Meia',          'preco': 750},
        ]
        validas = tl.filtrar_categorias_validas(categorias, 'pista')
        assert validas
        escolhida = min(validas, key=lambda c: c['preco'])
        assert escolhida['preco'] == 750

    def test_maior_limite_e_pista_inteira(self):
        assert tl.MAIOR_LIMITE == 4500

    def test_meia_nunca_maior_que_inteira_em_nenhum_setor(self):
        for setor, limites in tl.LIMITES_PRECO.items():
            assert limites['meia'] <= limites['inteira'], (
                f"Setor {setor}: meia ({limites['meia']}) > inteira ({limites['inteira']})"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PRIORIDADE DE SETOR
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrioridadeSetor:

    def test_ordem_completa_de_prioridade(self):
        setores = [
            {'nome': 'Arquibancada',     'preco_min': 400},
            {'nome': 'Cadeira Superior', 'preco_min': 500},
            {'nome': 'Cadeira Inferior', 'preco_min': 600},
            {'nome': 'Pista',            'preco_min': 900},
        ]
        keys = [s['key'] for s in tl.filtrar_setores_validos(setores)]
        assert keys == ['pista', 'cadeira_inferior', 'cadeira_superior', 'arquibancada']

    def test_sem_pista_ordem_restante(self):
        setores = [
            {'nome': 'Arquibancada',     'preco_min': 400},
            {'nome': 'Cadeira Superior', 'preco_min': 500},
            {'nome': 'Cadeira Inferior', 'preco_min': 600},
        ]
        keys = [s['key'] for s in tl.filtrar_setores_validos(setores)]
        assert keys == ['cadeira_inferior', 'cadeira_superior', 'arquibancada']

    def test_apenas_arquibancada(self):
        setores = [{'nome': 'Arquibancada', 'preco_min': 400}]
        validos = tl.filtrar_setores_validos(setores)
        assert len(validos) == 1 and validos[0]['key'] == 'arquibancada'

    def test_fluxo_tenta_proxima_combinacao_quando_primeira_falha(self, monkeypatch):
        """Se Pista falha, o fluxo deve tentar Arquibancada."""
        tentativas = []

        def processar_fake(driver, dia, url, setor):
            tentativas.append(setor['key'])
            return setor['key'] == 'arquibancada'

        monkeypatch.setattr(bot, 'buyticket_processar_combinacao', processar_fake)
        monkeypatch.setattr(bot, 'buyticket_ler_preco_botao', lambda d: 400)
        monkeypatch.setattr(bot, 'validar_botao_inicial', lambda p, l: (True, 'ok'))
        monkeypatch.setattr(bot, 'buyticket_ler_setores_disponiveis', lambda d: [])
        monkeypatch.setattr(bot, 'filtrar_setores_validos', lambda s, l=None: [
            {'nome': 'Pista',        'preco_min': 900, 'key': 'pista'},
            {'nome': 'Arquibancada', 'preco_min': 400, 'key': 'arquibancada'},
        ])

        ok = bot.buyticket_fluxo_compra(MagicMock(), '28/10', 'https://url?data=1')
        assert ok is True
        assert tentativas == ['pista', 'arquibancada']


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DRY_RUN — trava de segurança
# ═══════════════════════════════════════════════════════════════════════════════

class TestDryRun:

    def test_dry_run_retorna_false_sem_clicar_comprar(self, monkeypatch):
        monkeypatch.setattr(bot, 'DRY_RUN', True)
        driver = MagicMock()
        resultado = bot.buyticket_efetuar_compra(driver, '28/10', 'Pista', 'Inteira', 900)
        assert resultado is False
        driver.find_element.assert_not_called()

    def test_sem_dry_run_clica_comprar(self, monkeypatch):
        monkeypatch.setattr(bot, 'DRY_RUN', False)
        btn = MagicMock()
        driver = MagicMock()
        driver.find_element.return_value = btn
        driver.current_url = 'https://buyticketbrasil.com/checkout'
        monkeypatch.setattr(bot, 'buyticket_checkout', lambda *a, **k: True)
        resultado = bot.buyticket_efetuar_compra(driver, '28/10', 'Pista', 'Inteira', 900)
        btn.click.assert_called_once()
        assert resultado is True

    def test_dry_run_envia_notificacao_simulada(self, monkeypatch):
        monkeypatch.setattr(bot, 'DRY_RUN', True)
        notificacoes = []
        monkeypatch.setattr(bot, 'notificar', lambda titulo, *a, **k: notificacoes.append(titulo))
        bot.buyticket_efetuar_compra(MagicMock(), '28/10', 'Pista', 'Inteira', 900)
        assert any('DRY_RUN' in t or 'Simulad' in t for t in notificacoes)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. FILA DE ESPERA — detecção por múltiplos sinais
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilaDeEspera:

    @pytest.mark.parametrize('texto', [
        'Sua posição na fila: 150',
        'posicao na fila: 300',
        'Todo mundo quer nossos ingressos',
        'fila de espera virtual',
        'waiting room',
    ])
    def test_detecta_fila_por_texto(self, texto):
        driver = MagicMock()
        driver.page_source = texto
        assert bot._em_fila_de_espera(driver) is True

    def test_nao_detecta_fila_em_pagina_normal(self):
        driver = MagicMock()
        type(driver).page_source = PropertyMock(return_value='<html>Comprar ingresso R$ 900</html>')
        driver.find_elements.return_value = []
        assert bot._em_fila_de_espera(driver) is False

    def test_retorna_false_quando_page_source_lanca_excecao(self):
        driver = MagicMock()
        type(driver).page_source = PropertyMock(side_effect=Exception('stale'))
        driver.find_elements.return_value = []
        assert bot._em_fila_de_espera(driver) is False

    def test_aguardar_fila_retorna_true_quando_nao_ha_fila(self):
        driver = MagicMock()
        type(driver).page_source = PropertyMock(return_value='<html>página normal</html>')
        driver.find_elements.return_value = []
        assert bot._aguardar_sair_da_fila(driver, timeout_min=1) is True

    def test_aguardar_fila_timeout_retorna_false(self, monkeypatch):
        import time as _t
        t0 = _t.time()
        monkeypatch.setattr(bot.time, 'time', lambda: t0 + 9999)
        driver = MagicMock()
        type(driver).page_source = property(lambda s: 'posição na fila')
        assert bot._aguardar_sair_da_fila(driver, timeout_min=0) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 7. MODAL DE SEGURANÇA
# ═══════════════════════════════════════════════════════════════════════════════

class TestModalSeguranca:

    def test_fecha_pelo_botao_combinado(self):
        btn = MagicMock()
        btn.is_displayed.return_value = True
        driver = MagicMock()
        driver.find_elements.return_value = [btn]
        assert bot._fechar_modal_seguranca(driver) is True
        btn.click.assert_called_once()

    def test_retorna_false_sem_modal(self):
        driver = MagicMock()
        driver.find_elements.return_value = []
        assert bot._fechar_modal_seguranca(driver) is False

    def test_fecha_pelo_x_como_fallback(self):
        btn_x = MagicMock()
        btn_x.is_displayed.return_value = True

        def find_els(by, selector):
            if 'Combinado' in selector:
                return []
            return [btn_x]

        driver = MagicMock()
        driver.find_elements.side_effect = find_els
        assert bot._fechar_modal_seguranca(driver) is True
        btn_x.click.assert_called_once()

    def test_confirmar_avanco_retorna_true_quando_ja_avancou(self, monkeypatch):
        monkeypatch.setattr(bot, '_fechar_modal_seguranca', lambda d: False)
        monkeypatch.setattr(bot, '_ainda_na_tela_de_selecao', lambda d: False)
        assert bot._confirmar_avanco_checkout(MagicMock()) is True


# ═══════════════════════════════════════════════════════════════════════════════
# 8. VALIDAÇÃO FINAL — igualdade estrita
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidacaoFinal:

    def test_valores_iguais_liberam_compra(self):
        ok, motivo = tl.validar_valor_final(3300, 3300)
        assert ok is True and motivo == 'ok'

    def test_botao_acima_do_esperado_bloqueia(self):
        ok, motivo = tl.validar_valor_final(3400, 3300)
        assert ok is False and motivo == 'divergente'

    def test_botao_abaixo_do_esperado_bloqueia(self):
        ok, motivo = tl.validar_valor_final(3100, 3300)
        assert ok is False and motivo == 'divergente'

    def test_botao_sem_preco_bloqueia(self):
        ok, motivo = tl.validar_valor_final(None, 3300)
        assert ok is False and motivo == 'sem_preco'


# ═══════════════════════════════════════════════════════════════════════════════
# 9. SETOR SEM CATEGORIA VÁLIDA — não trava o fluxo
# ═══════════════════════════════════════════════════════════════════════════════

class TestSetorSemCategoriaValida:

    def test_fluxo_retorna_false_sem_travar(self, monkeypatch):
        monkeypatch.setattr(bot, 'buyticket_ler_preco_botao', lambda d: 900)
        monkeypatch.setattr(bot, 'validar_botao_inicial', lambda p, l: (True, 'ok'))
        monkeypatch.setattr(bot, 'buyticket_ler_setores_disponiveis', lambda d: [])
        monkeypatch.setattr(bot, 'filtrar_setores_validos', lambda s, l=None: [
            {'nome': 'Pista', 'preco_min': 900, 'key': 'pista'},
        ])
        monkeypatch.setattr(bot, 'buyticket_selecionar_e_confirmar', lambda *a, **k: True)
        monkeypatch.setattr(bot, 'buyticket_ler_categorias_disponiveis',
                            lambda d: [{'nome': 'Camarote Desconhecido', 'preco': 100}])
        monkeypatch.setattr(bot, 'filtrar_categorias_validas', lambda *a, **k: [])

        resultado = bot.buyticket_fluxo_compra(MagicMock(), '28/10', 'https://url?data=1')
        assert resultado is False
