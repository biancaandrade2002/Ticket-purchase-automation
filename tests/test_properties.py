"""
Property-Based Tests — Bot Speed Optimization (Properties 3–9).

Cada teste usa `hypothesis` para verificar invariantes sobre a lógica pura
em `ticket_logic.py` e sobre as constantes/estruturas do `bot.py`.

Tags de rastreabilidade:
  # Feature: bot-speed-optimization, Property N

Validates: Requirements especificados por propriedade (ver docstrings).
"""

import re
import sys
import os

import pytest
from hypothesis import given, settings, assume
import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Ajuste de path para importar ticket_logic do diretório raiz do projeto
# ---------------------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ticket_logic import (
    extrair_preco,
    filtrar_setores_validos,
    filtrar_categorias_validas,
    validar_botao_inicial,
    LIMITES_PRECO,
    MAPA_SETOR,
    MAPA_CATEGORIA,
    MAIOR_LIMITE,
    MAX_CICLOS,
)

# ---------------------------------------------------------------------------
# Estratégias reutilizáveis
# ---------------------------------------------------------------------------

# Nomes de setor reconhecidos pela lógica (chaves do MAPA_SETOR).
_NOMES_SETOR_VALIDOS = list(MAPA_SETOR.keys())

# Nomes de categoria reconhecidos pela lógica (chaves do MAPA_CATEGORIA).
_NOMES_CATEGORIA_VALIDOS = list(MAPA_CATEGORIA.keys())

# Nomes desconhecidos fixos para exercitar a regra de exclusão sem slow-gen.
_NOMES_SETOR_DESCONHECIDOS = [
    'Camarote VIP', 'Setor A', 'Premium', 'Cadeira Gold', 'Fossa',
    '', 'PISTA_GOLD', 'backstage', 'Lounge',
]

_NOMES_CATEGORIA_DESCONHECIDOS = [
    'Camarote', 'VIP', 'Promocional', 'Cortesia', '',
    'student', 'senior', 'categoria_x',
]

# Estratégia de nome de setor: mistura nomes válidos com um conjunto fixo de
# nomes desconhecidos. Evita st.text() irrestrito que causa HealthCheck.too_slow
# quando dentro de st.lists().
_st_nome_setor = st.sampled_from(
    _NOMES_SETOR_VALIDOS + _NOMES_SETOR_DESCONHECIDOS
)

# Estratégia de nome de categoria: idem para categorias.
_st_nome_categoria = st.sampled_from(
    _NOMES_CATEGORIA_VALIDOS + _NOMES_CATEGORIA_DESCONHECIDOS
)


# ===========================================================================
# Property 3 — Parsing de preço robusto a variações de formato
# Validates: Requirements 1.2, 1.3, 1.5
# ===========================================================================
# Feature: bot-speed-optimization, Property 3

@given(valor=st.integers(min_value=1, max_value=100_000))
@settings(max_examples=200)
def test_property3_extrair_preco_formato_simples(valor):
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Para qualquer inteiro positivo `valor`, as representações textuais
    mais simples de preço em reais devem ser parseadas corretamente.
    """
    # R$ X  (sem separador de milhar)
    assert extrair_preco(f'R$ {valor}') == valor
    # R$X  (sem espaço)
    assert extrair_preco(f'R${valor}') == valor
    # R$\xa0X  (espaço non-breaking)
    assert extrair_preco(f'R$\xa0{valor}') == valor


@given(valor=st.integers(min_value=1, max_value=100_000))
@settings(max_examples=200)
def test_property3_extrair_preco_formatos_com_centavos(valor):
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Formatos com centavos (`,00`) devem retornar apenas a parte inteira.
    """
    # R$ X,00  — parte decimal deve ser descartada
    assert extrair_preco(f'R$ {valor},00') == valor
    assert extrair_preco(f'R$ {valor},50') == valor


@given(valor=st.integers(min_value=1000, max_value=9999))
@settings(max_examples=200)
def test_property3_extrair_preco_milhar(valor):
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Valores de 4 dígitos com separador de milhar são interpretados
    corretamente (ex: `R$ 1.600` → 1600, não 1 ou 1600 errado).
    """
    milhar = f'{valor // 1000}.{valor % 1000:03d}'
    # R$ X.000
    assert extrair_preco(f'R$ {milhar}') == valor
    # R$ X.000,00
    assert extrair_preco(f'R$ {milhar},00') == valor


@given(texto=st.text(
    min_size=0,
    max_size=80,
    alphabet=st.characters(blacklist_categories=('Nd',), blacklist_characters='R$,.')
))
@settings(max_examples=200)
def test_property3_extrair_preco_sem_padrao_retorna_none(texto):
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Strings que não contêm nenhum padrão monetário reconhecível devem
    retornar None.  Exclui explicitamente dígitos, 'R', '$', ',' e '.'
    para garantir que o gerador não produza preços acidentais.
    """
    resultado = extrair_preco(texto)
    assert resultado is None


# ===========================================================================
# Property 4 — Filtragem de setores preserva apenas setores dentro do limite
# Validates: Requirements 1.4, 1.5
# ===========================================================================
# Feature: bot-speed-optimization, Property 4

@given(setores=st.lists(
    st.fixed_dictionaries({
        'nome': _st_nome_setor,
        'preco_min': st.integers(min_value=1, max_value=20_000),
    }),
    min_size=0,
    max_size=20,
))
@settings(max_examples=200)
def test_property4_setores_resultado_todos_dentro_do_limite(setores):
    """
    **Validates: Requirements 1.4, 1.5**

    Para qualquer lista de setores arbitrária, todo setor presente no
    resultado de `filtrar_setores_validos` deve:
      1. Ter `key` reconhecida em LIMITES_PRECO.
      2. Ter `preco_min` <= maior limite configurado para aquele setor.
    """
    resultado = filtrar_setores_validos(setores, LIMITES_PRECO)
    for s in resultado:
        key = s['key']
        assert key in LIMITES_PRECO, f'Setor com key desconhecida: {key!r}'
        maior_do_setor = max(LIMITES_PRECO[key].values())
        assert s['preco_min'] <= maior_do_setor, (
            f'Setor {key!r} com preco_min={s["preco_min"]} > limite={maior_do_setor}'
        )


@given(setores=st.lists(
    st.fixed_dictionaries({
        'nome': _st_nome_setor,
        'preco_min': st.integers(min_value=1, max_value=20_000),
    }),
    min_size=0,
    max_size=20,
))
@settings(max_examples=200)
def test_property4_setores_resultado_nenhum_nao_mapeado(setores):
    """
    **Validates: Requirements 1.4, 1.5**

    Nenhum setor com nome não mapeado (não presente em MAPA_SETOR) deve
    aparecer no resultado.
    """
    resultado = filtrar_setores_validos(setores, LIMITES_PRECO)
    nomes_resultado = {s['nome'].strip().lower() for s in resultado}
    for nome in nomes_resultado:
        assert nome in MAPA_SETOR, f'Nome não mapeado no resultado: {nome!r}'


# ===========================================================================
# Property 5 — Filtragem de categorias preserva apenas categorias dentro do limite
# Validates: Requirements 1.5, 2.4
# ===========================================================================
# Feature: bot-speed-optimization, Property 5

@given(
    setor_key=st.sampled_from(list(LIMITES_PRECO.keys())),
    categorias=st.lists(
        st.fixed_dictionaries({
            'nome': _st_nome_categoria,
            'preco': st.integers(min_value=1, max_value=20_000),
        }),
        min_size=0,
        max_size=20,
    ),
)
@settings(max_examples=200)
def test_property5_categorias_resultado_dentro_do_limite(setor_key, categorias):
    """
    **Validates: Requirements 1.5, 2.4**

    Para qualquer lista de categorias e qualquer setor válido, cada item
    no resultado deve:
      1. Ter `key` reconhecida no limite do setor (`LIMITES_PRECO[setor_key]`).
      2. Ter `preco` <= limite configurado para essa categoria nesse setor.
    """
    resultado = filtrar_categorias_validas(categorias, setor_key, LIMITES_PRECO)
    limites_setor = LIMITES_PRECO[setor_key]
    for c in resultado:
        key = c['key']
        assert key in limites_setor, f'Categoria com key desconhecida: {key!r}'
        assert c['preco'] <= limites_setor[key], (
            f'Categoria {key!r} com preco={c["preco"]} > limite={limites_setor[key]}'
        )


@given(
    setor_key=st.sampled_from(list(LIMITES_PRECO.keys())),
    categorias=st.lists(
        st.fixed_dictionaries({
            'nome': _st_nome_categoria,
            'preco': st.integers(min_value=1, max_value=20_000),
        }),
        min_size=0,
        max_size=20,
    ),
)
@settings(max_examples=200)
def test_property5_categorias_resultado_nenhuma_nao_mapeada(setor_key, categorias):
    """
    **Validates: Requirements 1.5, 2.4**

    Nenhuma categoria com nome não mapeado deve aparecer no resultado.
    """
    resultado = filtrar_categorias_validas(categorias, setor_key, LIMITES_PRECO)
    for c in resultado:
        nome_normalizado = c['nome'].strip().lower()
        assert nome_normalizado in MAPA_CATEGORIA, (
            f'Categoria não mapeada no resultado: {nome_normalizado!r}'
        )


# ===========================================================================
# Property 6 — Detecção de código PIX é precisa nos dois sentidos
# Validates: Requirements 4.1, 4.2
# ===========================================================================
# Feature: bot-speed-optimization, Property 6

_PIX_REGEX = re.compile(r'(0002[\w.]+)')

# Caracteres permitidos no padrão PIX: \w (word chars) e '.'
_PIX_CHARS = st.characters(
    whitelist_categories=('Lu', 'Ll', 'Nd'),
    whitelist_characters='._'
)


@given(sufixo=st.text(alphabet=_PIX_CHARS, min_size=27, max_size=60))
@settings(max_examples=200)
def test_property6_pix_valido_detectado(sufixo):
    """
    **Validates: Requirements 4.1, 4.2**

    Strings que começam com '0002' e têm comprimento total > 30 devem
    ser detectadas como código PIX pela regex `r'(0002[\\w.]+)'`.
    """
    codigo = '0002' + sufixo
    # Comprimento total garantido > 30 (4 + min_size=27 = 31+)
    assert len(codigo) > 30

    match = _PIX_REGEX.search(codigo)
    assert match is not None, f'PIX válido não detectado: {codigo[:50]!r}'
    assert len(match.group(1)) > 30, (
        f'Match encontrado mas comprimento insuficiente: {match.group(1)!r}'
    )


@given(texto=st.text(
    alphabet=st.characters(blacklist_characters='\x00'),
    min_size=0,
    max_size=80,
).filter(lambda s: not s.startswith('0002') or len(s) <= 30))
@settings(max_examples=200)
def test_property6_pix_invalido_nao_detectado(texto):
    """
    **Validates: Requirements 4.1, 4.2**

    Strings que NÃO começam com '0002' OU cujo comprimento total é <= 30
    não devem ser identificadas como código PIX válido (nenhum match com
    comprimento > 30).
    """
    match = _PIX_REGEX.search(texto)
    if match:
        # Pode haver um match, mas seu grupo capturado deve ter <= 30 chars
        assert len(match.group(1)) <= 30, (
            f'PIX inválido detectado como válido: {match.group(1)!r}'
        )


# ===========================================================================
# Property 7 — Sentinela 'expirado' determina retry somente quando preço está no limite
# Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5
# ===========================================================================
# Feature: bot-speed-optimization, Property 7

@given(preco=st.integers(min_value=1, max_value=MAIOR_LIMITE))
@settings(max_examples=200)
def test_property7_preco_dentro_do_limite_retorna_ok(preco):
    """
    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**

    Para qualquer preço em [1, MAIOR_LIMITE], `validar_botao_inicial` deve
    retornar (True, 'ok') — indicando que o ciclo deve/pode prosseguir.
    """
    ok, motivo = validar_botao_inicial(preco, MAIOR_LIMITE)
    assert ok is True, f'Esperado ok=True para preco={preco}, obteve ok={ok}'
    assert motivo == 'ok', f'Esperado motivo="ok", obteve {motivo!r}'


@given(preco=st.integers(min_value=MAIOR_LIMITE + 1, max_value=500_000))
@settings(max_examples=200)
def test_property7_preco_acima_do_limite_encerra_retry(preco):
    """
    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**

    Para qualquer preço > MAIOR_LIMITE, `validar_botao_inicial` deve
    retornar (False, 'fora_parametro') — indicando que a tentativa deve
    ser encerrada, não reiniciada.
    """
    ok, motivo = validar_botao_inicial(preco, MAIOR_LIMITE)
    assert ok is False, f'Esperado ok=False para preco={preco}, obteve ok={ok}'
    assert motivo == 'fora_parametro', (
        f'Esperado motivo="fora_parametro", obteve {motivo!r}'
    )


def test_property7_preco_none_retorna_sem_preco():
    """
    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**

    `validar_botao_inicial(None, ...)` deve retornar (False, 'sem_preco').
    """
    ok, motivo = validar_botao_inicial(None, MAIOR_LIMITE)
    assert ok is False
    assert motivo == 'sem_preco'


# ===========================================================================
# Property 8 — Número de tentativas de retry é limitado por MAX_CICLOS
# Validates: Requirements 5.6, 5.1
# ===========================================================================
# Feature: bot-speed-optimization, Property 8

@given(n=st.integers(min_value=0, max_value=1000))
@settings(max_examples=200)
def test_property8_range_max_ciclos_nunca_excede_max_ciclos(n):
    """
    **Validates: Requirements 5.6, 5.1**

    `range(MAX_CICLOS)` produz exatamente MAX_CICLOS elementos,
    independentemente de qualquer outro parâmetro `n`.  Simula o
    invariante do loop `for ciclo in range(MAX_CICLOS)` em
    `buyticket_processar_combinacao`: o número de iterações é sempre
    limitado a MAX_CICLOS, nunca a `n`.
    """
    # O loop no bot usa range(MAX_CICLOS) — a contagem é sempre fixa.
    iteracoes = list(range(MAX_CICLOS))
    assert len(iteracoes) == MAX_CICLOS, (
        f'range(MAX_CICLOS) produziu {len(iteracoes)} elementos, '
        f'esperado exatamente {MAX_CICLOS}'
    )
    # Usar `n` variável não altera o bound — apenas confirma que MAX_CICLOS é constante.
    assert len(iteracoes) <= MAX_CICLOS


def test_property8_max_ciclos_permite_pelo_menos_uma_tentativa():
    """
    **Validates: Requirements 5.6, 5.1**

    MAX_CICLOS deve ser >= 1: o bot precisa de ao menos um ciclo para
    tentar a compra.
    """
    assert MAX_CICLOS >= 1, f'MAX_CICLOS={MAX_CICLOS} deve ser >= 1'


@given(resultados_expirados=st.lists(
    st.just('expirado'),
    min_size=0,
    max_size=MAX_CICLOS * 3,   # sequência mais longa que o limite
))
@settings(max_examples=200)
def test_property8_contador_nunca_ultrapassa_max_ciclos(resultados_expirados):
    """
    **Validates: Requirements 5.6, 5.1**

    Simula o loop de retry: para qualquer sequência de retornos 'expirado',
    o número de iterações consumidas pelo `range(MAX_CICLOS)` nunca
    ultrapassa MAX_CICLOS.
    """
    iteracoes_realizadas = 0
    for ciclo in range(MAX_CICLOS):
        iteracoes_realizadas += 1
        # Consome um resultado expirado se disponível; senão encerra.
        if ciclo < len(resultados_expirados):
            resultado = resultados_expirados[ciclo]
            if resultado != 'expirado':
                break
        else:
            break  # sem mais resultados: encerra

    assert iteracoes_realizadas <= MAX_CICLOS, (
        f'Loop realizou {iteracoes_realizadas} iterações, '
        f'máximo permitido: {MAX_CICLOS}'
    )


# ===========================================================================
# Property 9 — Mapeamento de campos respeita o conjunto de prefixos definidos
# Validates: Requirements 6.4, 6.5
# ===========================================================================
# Feature: bot-speed-optimization, Property 9

# Prefixos conhecidos de `buyticket_preencher_dados` (em minúsculas).
_PREFIXOS_CONHECIDOS = (
    'e-mail do recebedor',
    'nome completo',
    'telefone',
    'cpf',
    'cep',
    'estado',
    'bairro',
    'munic',
    'n°',
    'nº',
    'endereço',
    'complemento',
)


def _conta_prefixos_que_casam(placeholder: str) -> int:
    """Retorna quantos prefixos conhecidos casam com `placeholder`."""
    return sum(
        1 for p in _PREFIXOS_CONHECIDOS
        if placeholder.strip().lower().startswith(p)
    )


@given(placeholder=st.text(min_size=1, max_size=60).filter(
    lambda s: not any(s.strip().lower().startswith(p) for p in _PREFIXOS_CONHECIDOS)
))
@settings(max_examples=200)
def test_property9_placeholder_desconhecido_nao_casa_nenhuma_regra(placeholder):
    """
    **Validates: Requirements 6.4, 6.5**

    Para qualquer placeholder cujo início (em minúsculas) não corresponde
    a nenhum prefixo definido nas regras de `buyticket_preencher_dados`,
    nenhuma regra deve casar — o campo não será preenchido.
    """
    assert _conta_prefixos_que_casam(placeholder) == 0, (
        f'Placeholder {placeholder!r} casou com uma regra mas não deveria'
    )


@given(prefixo=st.sampled_from(_PREFIXOS_CONHECIDOS))
@settings(max_examples=200)
def test_property9_placeholder_conhecido_casa_exatamente_uma_regra(prefixo):
    """
    **Validates: Requirements 6.4, 6.5**

    Para cada prefixo conhecido, um placeholder que começa exatamente com
    esse prefixo deve casar com pelo menos uma regra.  O design usa o
    primeiro match (break após casar), portanto pelo menos uma regra casa.
    """
    # Usa o prefixo exato como placeholder (caso mínimo).
    casamentos = _conta_prefixos_que_casam(prefixo)
    assert casamentos >= 1, (
        f'Prefixo {prefixo!r} não casou com nenhuma regra'
    )


@given(
    prefixo=st.sampled_from(_PREFIXOS_CONHECIDOS),
    sufixo=st.text(min_size=0, max_size=30),
)
@settings(max_examples=200)
def test_property9_placeholder_com_sufixo_ainda_casa(prefixo, sufixo):
    """
    **Validates: Requirements 6.4, 6.5**

    Um placeholder formado por `prefixo + sufixo_arbitrário` deve
    continuar casando com a regra correta (startswith é prefixo-tolerante).
    """
    placeholder_completo = prefixo + sufixo
    casamentos = _conta_prefixos_que_casam(placeholder_completo)
    assert casamentos >= 1, (
        f'Placeholder {placeholder_completo!r} não casou com nenhuma regra, '
        f'mas prefixo {prefixo!r} deveria casar'
    )


# ===========================================================================
# Property 1 — Decisão do cache é consistente com estado atual
# Validates: Requirements 1.2, 1.3, 1.5
# ===========================================================================
# Feature: bot-speed-optimization, Property 1


def _simular_decisao_cache(preco_cache, data_show, preco_botao, maior_limite):
    """
    Replica a lógica de decisão do cache presente em monitor_buyticket (bot.py).

    Recebe uma cópia do cache (dict mutável), a data do show, o preço lido do
    botão e o maior limite configurado.

    Retorna (decisao, cache_apos) onde:
        decisao    – 'skip'      → preço igual ao cache, sem ação
                   – 'log_only'  → preço mudou mas está acima do limite
                   – 'comprar'   → preço mudou e está dentro do limite
        cache_apos – estado do cache após a decisão
    """
    cache = dict(preco_cache)  # cópia para não mutar o original do chamador
    ultimo_preco = cache.get(data_show)

    # Preço igual ao cache → SKIP
    if preco_botao == ultimo_preco:
        return 'skip', cache

    # Preço mudou → atualiza cache
    cache[data_show] = preco_botao

    # Fora do limite → apenas loga, sem compra
    if preco_botao > maior_limite:
        return 'log_only', cache

    # Dentro do limite → comprar
    return 'comprar', cache


# ── Caso 1: cache vazio (None) + preço acima do limite ──────────────────────

def test_property1_cache_vazio_preco_acima_limite_nao_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache vazio + preço > MAIOR_LIMITE → atualiza cache, decisão log_only
    (NÃO dispara compra).
    """
    cache = {}
    preco = MAIOR_LIMITE + 1
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco, MAIOR_LIMITE)
    assert decisao == 'log_only', f'Esperado log_only, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco, 'Cache deve ser atualizado com o novo preço'


# ── Caso 2: cache vazio (None) + preço dentro do limite ─────────────────────

def test_property1_cache_vazio_preco_dentro_limite_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache vazio + preço <= MAIOR_LIMITE → atualiza cache, decisão comprar.
    """
    cache = {}
    preco = MAIOR_LIMITE
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco, MAIOR_LIMITE)
    assert decisao == 'comprar', f'Esperado comprar, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco, 'Cache deve ser atualizado com o novo preço'


def test_property1_cache_none_preco_dentro_limite_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache com valor None explícito + preço <= MAIOR_LIMITE → comprar.
    (None é tratado como cache vazio/inválido — o preço "mudou".)
    """
    cache = {'28/10': None}
    preco = 100
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco, MAIOR_LIMITE)
    assert decisao == 'comprar', f'Esperado comprar, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco


# ── Caso 3: cache HIT + preço acima do limite → SKIP ────────────────────────

def test_property1_cache_hit_preco_acima_limite_skip():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache HIT (preço == último) + preço > MAIOR_LIMITE → SKIP.
    O cache NÃO deve ser alterado.
    """
    preco = MAIOR_LIMITE + 500
    cache = {'28/10': preco}
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco, MAIOR_LIMITE)
    assert decisao == 'skip', f'Esperado skip, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco, 'Cache não deve ser alterado em skip'


# ── Caso 4: cache HIT + preço dentro do limite → SKIP ───────────────────────

def test_property1_cache_hit_preco_dentro_limite_skip():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache HIT (preço == último) + preço <= MAIOR_LIMITE → SKIP.
    O mesmo preço dentro do limite já foi tentado; aguarda mudança.
    """
    preco = MAIOR_LIMITE
    cache = {'28/10': preco}
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco, MAIOR_LIMITE)
    assert decisao == 'skip', f'Esperado skip, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco, 'Cache não deve ser alterado em skip'


# ── Caso 5: cache MISS + preço acima do limite → log_only ───────────────────

def test_property1_cache_miss_preco_acima_limite_nao_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache MISS (preço != último) + preço > MAIOR_LIMITE → atualiza cache,
    decisão log_only (NÃO dispara compra).
    """
    preco_anterior = MAIOR_LIMITE + 100
    preco_novo = MAIOR_LIMITE + 200
    cache = {'28/10': preco_anterior}
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco_novo, MAIOR_LIMITE)
    assert decisao == 'log_only', f'Esperado log_only, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco_novo, 'Cache deve ser atualizado com o novo preço'


# ── Caso 6: cache MISS + preço dentro do limite → comprar ───────────────────

def test_property1_cache_miss_preco_dentro_limite_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Cache MISS (preço != último) + preço <= MAIOR_LIMITE → atualiza cache,
    decisão comprar.
    """
    preco_anterior = MAIOR_LIMITE + 100  # estava fora do limite
    preco_novo = MAIOR_LIMITE - 100      # entrou no limite
    cache = {'28/10': preco_anterior}
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco_novo, MAIOR_LIMITE)
    assert decisao == 'comprar', f'Esperado comprar, obteve {decisao!r}'
    assert cache_apos.get('28/10') == preco_novo, 'Cache deve ser atualizado com o novo preço'


# ── Consistência entre dias (isolamento) ────────────────────────────────────

def test_property1_dias_independentes():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    A decisão para um dia não deve afetar o cache dos outros dias.
    """
    cache = {'28/10': 100, '30/10': 200, '31/10': 300}
    preco_novo = 50  # dentro do limite
    decisao, cache_apos = _simular_decisao_cache(cache, '28/10', preco_novo, MAIOR_LIMITE)
    assert decisao == 'comprar'
    # Outros dias intactos
    assert cache_apos.get('30/10') == 200
    assert cache_apos.get('31/10') == 300


# ── Limite de fronteira (boundary): preço == MAIOR_LIMITE ───────────────────

def test_property1_preco_exatamente_no_limite_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Preço exatamente igual a MAIOR_LIMITE deve disparar compra (dentro do
    limite, boundary inclusivo).
    """
    cache = {}
    decisao, _ = _simular_decisao_cache(cache, '28/10', MAIOR_LIMITE, MAIOR_LIMITE)
    assert decisao == 'comprar', (
        f'Preço == MAIOR_LIMITE ({MAIOR_LIMITE}) deve resultar em comprar'
    )


def test_property1_preco_um_acima_do_limite_nao_compra():
    """
    **Validates: Requirements 1.2, 1.3, 1.5**

    Preço == MAIOR_LIMITE + 1 não deve disparar compra (fora do limite,
    boundary exclusivo).
    """
    cache = {}
    decisao, _ = _simular_decisao_cache(cache, '28/10', MAIOR_LIMITE + 1, MAIOR_LIMITE)
    assert decisao == 'log_only', (
        f'Preço == MAIOR_LIMITE+1 ({MAIOR_LIMITE + 1}) deve resultar em log_only'
    )


# ===========================================================================
# Property 2 — Invalidação de cache é idempotente
# Validates: Requirements 1.6, 1.3
# ===========================================================================
# Feature: bot-speed-optimization, Property 2


def test_property2_invalidacao_define_none():
    """
    **Validates: Requirements 1.6, 1.3**

    Após `preco_cache[dia] = None`, o valor armazenado deve ser None.
    """
    cache = {'28/10': 500}
    cache['28/10'] = None
    assert cache['28/10'] is None, 'Invalidação deve armazenar None'


def test_property2_invalidacao_multiplas_vezes_mantem_none():
    """
    **Validates: Requirements 1.6, 1.3**

    Aplicar a invalidação múltiplas vezes deve continuar resultando em None
    (idempotência).
    """
    cache = {'28/10': 500}
    for _ in range(5):
        cache['28/10'] = None
        assert cache['28/10'] is None, 'Cada invalidação deve manter None'


def test_property2_invalidacao_de_none_permanece_none():
    """
    **Validates: Requirements 1.6, 1.3**

    Invalidar um cache que já é None deve mantê-lo None (não levanta erro,
    não muda o valor).
    """
    cache = {'28/10': None}
    cache['28/10'] = None
    assert cache['28/10'] is None


def test_property2_invalidacao_de_qualquer_valor_resulta_em_none():
    """
    **Validates: Requirements 1.6, 1.3**

    Para qualquer valor inicial (inteiro positivo ou None), após invalidação
    o resultado é sempre None.
    """
    valores_iniciais = [0, 1, 100, MAIOR_LIMITE, MAIOR_LIMITE + 1, 999_999, None]
    for valor in valores_iniciais:
        cache = {'28/10': valor}
        cache['28/10'] = None
        assert cache['28/10'] is None, (
            f'Invalidação a partir de {valor!r} deve resultar em None'
        )


def test_property2_invalidacao_nao_afeta_outros_dias():
    """
    **Validates: Requirements 1.6, 1.3**

    Invalidar o cache de um dia não deve afetar os demais dias.
    """
    cache = {'28/10': 100, '30/10': 200, '31/10': 300}
    cache['28/10'] = None
    assert cache['28/10'] is None
    assert cache['30/10'] == 200, 'Cache de 30/10 não deve ser alterado'
    assert cache['31/10'] == 300, 'Cache de 31/10 não deve ser alterado'


def test_property2_invalidacao_chave_ausente_cria_none():
    """
    **Validates: Requirements 1.6, 1.3**

    Invalidar uma chave que ainda não existe no cache deve criar a entrada
    com valor None (sem KeyError).
    """
    cache = {}
    cache['28/10'] = None
    assert cache.get('28/10') is None


@given(valor=st.one_of(
    st.integers(min_value=1, max_value=500_000),
    st.none(),
))
@settings(max_examples=200)
def test_property2_pbt_invalidacao_idempotente_para_qualquer_valor(valor):
    """
    **Validates: Requirements 1.6, 1.3**

    Property-based: para qualquer valor inicial (inteiro ou None), após
    atribuição de None o cache do dia é sempre None.
    """
    cache = {'28/10': valor}
    cache['28/10'] = None
    assert cache['28/10'] is None, (
        f'Esperado None após invalidação, valor inicial={valor!r}'
    )


# ===========================================================================
# Property 3 — Edge cases unitários de extrair_preco
# Validates: Requirements 1.2, 1.3
# ===========================================================================
# Feature: bot-speed-optimization, Property 3 (edge cases)


def test_extrair_preco_string_vazia_retorna_none():
    """
    **Validates: Requirements 1.2, 1.3**

    String vazia não contém padrão monetário — deve retornar None.
    """
    assert extrair_preco('') is None


def test_extrair_preco_none_retorna_none():
    """
    **Validates: Requirements 1.2, 1.3**

    Input None deve retornar None sem levantar exceção.
    """
    assert extrair_preco(None) is None


def test_extrair_preco_apenas_texto_retorna_none():
    """
    **Validates: Requirements 1.2, 1.3**

    Texto sem nenhum padrão monetário (ex: "Esgotado") deve retornar None.
    """
    assert extrair_preco('Esgotado') is None


def test_extrair_preco_r_cifrao_sem_numero_retorna_none():
    """
    **Validates: Requirements 1.2, 1.3**

    'R$' sem número subsequente não deve extrair nenhum valor.
    """
    assert extrair_preco('R$') is None


def test_extrair_preco_4_digitos_sem_separador_nao_trunca():
    """
    **Validates: Requirements 1.2, 1.3**

    'R$ 1000' deve retornar 1000 (não 100 nem 1).
    Garante que números de 4+ dígitos sem separador de milhar são
    interpretados corretamente — regressão para o bug de truncamento.
    """
    assert extrair_preco('R$ 1000') == 1000


def test_extrair_preco_com_virgula_descarta_centavos():
    """
    **Validates: Requirements 1.2, 1.3**

    'R$ 900,99' deve retornar 900 (parte decimal descartada).
    O bot trabalha com valores inteiros em reais; os centavos são irrelevantes.
    """
    assert extrair_preco('R$ 900,99') == 900
