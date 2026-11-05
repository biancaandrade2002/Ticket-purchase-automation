"""
Testes da lógica pura de seleção/validação de ingressos (ticket_logic.py).

Cobrem os cenários BDD da spec:
- Tabela de limites correta e MAIOR_LIMITE derivado (Task 1).
- Parsing de preço em vários formatos e entradas inválidas (Task 2).
- Validação inicial do botão dentro/fora/sem preço (Task 3).
- Filtragem de Tipos com exclusão e priorização por setor (Task 5).
- Filtragem de Categorias com exclusão e limite (Task 6).
- Validação final estrita: igual libera, divergente refaz (Task 7).
"""

import pytest

import ticket_logic as tl


# ============================================================
# Task 1 - Tabela de limites (fonte de verdade) e MAIOR_LIMITE
# ============================================================

def test_tabela_limites_alinhada_ao_readme():
    assert tl.LIMITES_PRECO['arquibancada'] == {'inteira': 600, 'meia': 500}
    assert tl.LIMITES_PRECO['cadeira_superior'] == {'inteira': 900, 'meia': 600}
    assert tl.LIMITES_PRECO['cadeira_inferior'] == {'inteira': 1100, 'meia': 700}
    assert tl.LIMITES_PRECO['pista'] == {'inteira': 5000, 'meia': 1000}


def test_meia_nunca_maior_que_inteira():
    # Correção da inconsistência antiga (arquibancada tinha meia > inteira).
    for setor, limites in tl.LIMITES_PRECO.items():
        assert limites['meia'] <= limites['inteira'], setor


def test_maior_limite_derivado():
    assert tl.MAIOR_LIMITE == 5000
    assert tl.calcular_maior_limite() == 5000


def test_calcular_maior_limite_customizado():
    custom = {'x': {'inteira': 50, 'meia': 30}, 'y': {'inteira': 80, 'meia': 20}}
    assert tl.calcular_maior_limite(custom) == 80


# ============================================================
# Task 2 - Parsing de preço
# ============================================================

@pytest.mark.parametrize('texto,esperado', [
    ('R$ 1.600', 1600),
    ('R$1.600', 1600),
    ('R$\xa01.600', 1600),
    ('R$ 2.991', 2991),
    ('R$ 600', 600),
    ('R$600', 600),
    ('R$ 1.100.000', 1100000),
    ('Comprar por R$ 500,00', 500),
    ('1.600', 1600),
    ('R$ 1000', 1000),        # 4+ dígitos sem separador NÃO deve truncar
    ('R$ 1000,50', 1000),     # com centavos, sem milhar
    ('R$ 1.000,00', 1000),    # milhar + centavos
    ('Comprar R$ 12345', 12345),
])
def test_extrair_preco_formatos_validos(texto, esperado):
    assert tl.extrair_preco(texto) == esperado


def test_extrair_preco_nao_trunca_milhares_sem_separador():
    # Regressão: "R$ 1000" já foi truncado para 100 pela regex antiga.
    assert tl.extrair_preco('R$ 1000') == 1000
    assert tl.extrair_preco('R$ 1600') == 1600


@pytest.mark.parametrize('texto', [
    None, '', 'Esgotado', 'Indisponível', 'abc', 'R$',
])
def test_extrair_preco_invalido_retorna_none(texto):
    assert tl.extrair_preco(texto) is None


# ============================================================
# Task 3 - Validação inicial do botão
# ============================================================

def test_validar_botao_dentro():
    ok, motivo = tl.validar_botao_inicial(1500, 1600)
    assert ok is True and motivo == 'ok'


def test_validar_botao_no_limite_exato():
    ok, motivo = tl.validar_botao_inicial(1600, 1600)
    assert ok is True and motivo == 'ok'


def test_validar_botao_fora():
    ok, motivo = tl.validar_botao_inicial(2000, 1600)
    assert ok is False and motivo == 'fora_parametro'


def test_validar_botao_sem_preco():
    ok, motivo = tl.validar_botao_inicial(None, 1600)
    assert ok is False and motivo == 'sem_preco'


def test_validar_botao_usa_maior_limite_default():
    # Sem passar maior_limite, usa tl.MAIOR_LIMITE (5000).
    assert tl.validar_botao_inicial(5000)[0] is True
    assert tl.validar_botao_inicial(5001)[0] is False


# ============================================================
# Task 5 - Filtragem e priorização de Tipos (setores)
# ============================================================

def test_filtrar_setores_ignora_nao_mapeado():
    setores = [
        {'nome': 'Camarote VIP', 'preco_min': 100},  # não mapeado -> ignora
        {'nome': 'Arquibancada', 'preco_min': 450},
    ]
    validos = tl.filtrar_setores_validos(setores)
    nomes = [s['nome'] for s in validos]
    assert 'Camarote VIP' not in nomes
    assert 'Arquibancada' in nomes


def test_filtrar_setores_ignora_fora_do_limite():
    setores = [
        {'nome': 'Arquibancada', 'preco_min': 9999},  # acima do maior limite do setor
    ]
    assert tl.filtrar_setores_validos(setores) == []


def test_filtrar_setores_ignora_sem_preco():
    setores = [{'nome': 'Pista', 'preco_min': None}]
    assert tl.filtrar_setores_validos(setores) == []


def test_filtrar_setores_prioridade_ordem():
    # Ordem embaralhada na entrada; saída deve respeitar a prioridade.
    setores = [
        {'nome': 'Arquibancada', 'preco_min': 400},
        {'nome': 'Cadeira Superior', 'preco_min': 500},
        {'nome': 'Pista', 'preco_min': 900},
        {'nome': 'Cadeira Inferior', 'preco_min': 600},
    ]
    validos = tl.filtrar_setores_validos(setores)
    keys = [s['key'] for s in validos]
    assert keys == ['pista', 'cadeira_inferior', 'cadeira_superior', 'arquibancada']


def test_filtrar_setores_adiciona_key():
    setores = [{'nome': 'Pista', 'preco_min': 900}]
    validos = tl.filtrar_setores_validos(setores)
    assert validos[0]['key'] == 'pista'


# ============================================================
# Task 6 - Filtragem de Categorias
# ============================================================

def test_filtrar_categorias_dentro_do_limite():
    categorias = [
        {'nome': 'Inteira', 'preco': 550},   # <= 600 (arquibancada inteira)
        {'nome': 'Meia', 'preco': 480},      # <= 500 (arquibancada meia)
    ]
    validas = tl.filtrar_categorias_validas(categorias, 'arquibancada')
    nomes = [c['nome'] for c in validas]
    assert 'Inteira' in nomes and 'Meia' in nomes


def test_filtrar_categorias_ignora_acima_do_limite():
    categorias = [{'nome': 'Inteira', 'preco': 700}]  # > 600 (arquibancada)
    assert tl.filtrar_categorias_validas(categorias, 'arquibancada') == []


def test_filtrar_categorias_ignora_nao_mapeada():
    categorias = [{'nome': 'Camarote', 'preco': 100}]
    assert tl.filtrar_categorias_validas(categorias, 'pista') == []


def test_filtrar_categorias_ignora_preco_zero_ou_none():
    categorias = [
        {'nome': 'Inteira', 'preco': 0},
        {'nome': 'Meia', 'preco': None},
    ]
    assert tl.filtrar_categorias_validas(categorias, 'pista') == []


def test_filtrar_categorias_meia_estudante_mapeia_para_meia():
    categorias = [{'nome': 'Meia Estudante', 'preco': 900}]  # <= 1000 (pista meia)
    validas = tl.filtrar_categorias_validas(categorias, 'pista')
    assert len(validas) == 1
    assert validas[0]['key'] == 'meia'
    assert validas[0]['limite'] == 1000


# ============================================================
# Task 7 - Validação final estrita
# ============================================================

def test_validar_valor_final_igual_libera():
    ok, motivo = tl.validar_valor_final(1000, 1000)
    assert ok is True and motivo == 'ok'


def test_validar_valor_final_divergente():
    ok, motivo = tl.validar_valor_final(1200, 1000)
    assert ok is False and motivo == 'divergente'


def test_validar_valor_final_sem_preco_botao():
    ok, motivo = tl.validar_valor_final(None, 1000)
    assert ok is False and motivo == 'sem_preco'


# ============================================================
# Checagem periódica de login (padrão de tempo)
# ============================================================

def test_intervalo_check_login_default_uma_hora():
    assert tl.INTERVALO_CHECK_LOGIN_S == 3600


def test_deve_checar_login_primeira_vez_sem_referencia():
    # ultima_checagem None -> sempre checa (primeira verificação).
    assert tl.deve_checar_login(agora=1000, ultima_checagem=None) is True


def test_deve_checar_login_antes_do_intervalo():
    # Menos de 1h decorrida -> não checa ainda.
    assert tl.deve_checar_login(agora=1000, ultima_checagem=1000) is False
    assert tl.deve_checar_login(agora=1000 + 3599, ultima_checagem=1000) is False


def test_deve_checar_login_no_limite_exato():
    # Exatamente 1h -> checa.
    assert tl.deve_checar_login(agora=1000 + 3600, ultima_checagem=1000) is True


def test_deve_checar_login_apos_intervalo():
    assert tl.deve_checar_login(agora=1000 + 7200, ultima_checagem=1000) is True


def test_deve_checar_login_intervalo_customizado():
    # Intervalo curto (ex: 10s) para configuração via .env.
    assert tl.deve_checar_login(agora=100, ultima_checagem=95, intervalo=10) is False
    assert tl.deve_checar_login(agora=100, ultima_checagem=90, intervalo=10) is True


# ============================================================
# url_e_do_dia - refresh não pode "voltar ao início"
# ============================================================

_URL_28 = ('https://buyticketbrasil.com/evento/bts?data=1793242799000'
           '&evento_local=abc&cidade=Sao+Paulo')
_URL_30 = ('https://buyticketbrasil.com/evento/bts?data=1793415599000'
           '&evento_local=abc&cidade=Sao+Paulo')


def test_url_e_do_dia_mesma_data():
    assert tl.url_e_do_dia(_URL_28, _URL_28) is True


def test_url_e_do_dia_data_diferente():
    # Caiu na página de outro dia -> não é o dia esperado.
    assert tl.url_e_do_dia(_URL_30, _URL_28) is False


def test_url_e_do_dia_menu_inicial_sem_data():
    # Refresh caiu no menu inicial (sem parâmetro data) -> False.
    assert tl.url_e_do_dia('https://buyticketbrasil.com/', _URL_28) is False


def test_url_e_do_dia_url_atual_vazia():
    assert tl.url_e_do_dia('', _URL_28) is False
    assert tl.url_e_do_dia(None, _URL_28) is False


def test_url_e_do_dia_esperada_sem_data_usa_caminho_base():
    base = 'https://buyticketbrasil.com/entrar'
    assert tl.url_e_do_dia(base + '?x=1', base) is True
    assert tl.url_e_do_dia('https://buyticketbrasil.com/outra', base) is False


# ============================================================
# MAX_RETRIES — constante de retentativas
# ============================================================

def test_max_retries_igual_3():
    assert tl.MAX_RETRIES == 3


# ============================================================
# Cronometro — telemetria por etapa
# ============================================================

import time as _time_mod


def test_cronometro_registra_duracao():
    tl.Cronometro.resetar()
    with tl.Cronometro('teste_registra'):
        pass  # duração ~0s
    reg = tl.Cronometro._registro
    assert 'teste_registra' in reg
    assert len(reg['teste_registra']) == 1
    assert reg['teste_registra'][0] >= 0.0


def test_cronometro_acumula_multiplas_medicoes():
    tl.Cronometro.resetar()
    for _ in range(3):
        with tl.Cronometro('multi'):
            pass
    assert len(tl.Cronometro._registro['multi']) == 3


def test_cronometro_relatorio_formato():
    tl.Cronometro.resetar()
    # Injeta valores conhecidos diretamente no registro para testar o relatório.
    with tl.Cronometro._lock:
        tl.Cronometro._registro['etapa_rapida'] = [0.1, 0.2, 0.3]
        tl.Cronometro._registro['etapa_lenta'] = [6.0, 20.0]  # deve marcar gargalo
    rel = tl.Cronometro.relatorio()
    assert 'etapa_rapida' in rel
    assert 'etapa_lenta' in rel
    assert '⚠️' in rel  # gargalo detectado na etapa_lenta


def test_cronometro_resetar_limpa_registro():
    with tl.Cronometro('antes_reset'):
        pass
    tl.Cronometro.resetar()
    assert tl.Cronometro._registro == {}


def test_cronometro_relatorio_vazio():
    tl.Cronometro.resetar()
    rel = tl.Cronometro.relatorio()
    assert 'sem medições' in rel or 'sem medi' in rel.lower()
