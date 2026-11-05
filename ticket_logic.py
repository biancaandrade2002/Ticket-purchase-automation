"""
Lógica pura do fluxo de seleção e validação de ingressos (BuyTicket).

Este módulo NÃO depende de Selenium, requests ou navegador. Ele concentra
toda a lógica de negócio testável:

- Tabela de limites de preço (ajustável, fonte de verdade única).
- Parsing de preço a partir de texto da interface.
- Mapeamento e regra de exclusão de setores/categorias não previstos.
- Filtragem por limite e priorização por setor.
- Validação inicial do botão de compra.
- Validação final estrita (botão == preço da categoria).
- Classe Cronometro: telemetria de latência por etapa (thread-safe).

O `bot.py` importa deste módulo e cuida apenas da orquestração via Selenium.
"""

import re
import os
import threading
import time as _time

# ============================================================
# TABELA DE LIMITES (FONTE DE VERDADE - AJUSTÁVEL)
# ============================================================
#
# SEM LIMITE DE PREÇO: todos os setores aceitam qualquer valor.
# Os limites estão definidos como 999_999 (teto fictício alto) para que
# nenhum ingresso seja descartado pela validação de preço.
# Para reativar limites reais, basta reduzir os valores abaixo.
#
#   | Setor            | Inteira (máx) | Meia (máx)  |
#   |------------------|---------------|-------------|
#   | Soundcheck       | sem limite    | sem limite  |  ← PRIORIDADE 1
#   | Pista            | sem limite    | sem limite  |  ← PRIORIDADE 2
#   | Cadeira Inferior | sem limite    | sem limite  |
#   | Cadeira Superior | sem limite    | sem limite  |
#   | Arquibancada     | sem limite    | sem limite  |
#
LIMITES_PRECO = {
    'soundcheck':      {'inteira': 999_999, 'meia': 999_999},  # PRIORIDADE 1
    'pista':           {'inteira': 999_999, 'meia': 999_999},  # PRIORIDADE 2
    'cadeira_inferior': {'inteira': 999_999, 'meia': 999_999},
    'cadeira_superior': {'inteira': 999_999, 'meia': 999_999},
    'arquibancada':    {'inteira': 999_999, 'meia': 999_999},
}

# Prioridade de escolha entre combinações válidas (índice menor = maior
# prioridade). Soundcheck > Pista > Cadeira Inferior > Cadeira Superior > Arquibancada.
PRIORIDADE_SETOR = {
    'soundcheck':      0,  # ← PRIORIDADE MÁXIMA
    'pista':           1,  # ← PRIORIDADE 2 (qualquer dia)
    'cadeira_inferior': 2,
    'cadeira_superior': 3,
    'arquibancada':    4,
}

# Número máximo de ciclos completos (recarregar + refazer) por combinação.
MAX_CICLOS = 3

# Número máximo de retentativas por etapa/elemento (login, seleção, cliques).
# Usado uniformemente em todas as operações de interação crítica do bot.
MAX_RETRIES = 3

# Intervalo padrão (em segundos) entre checagens periódicas de sessão/login.
# A cada 30 minutos verifica-se se o login continua ativo. Ajustável via .env.
INTERVALO_CHECK_LOGIN_S = 1800


def deve_checar_login(agora, ultima_checagem, intervalo=INTERVALO_CHECK_LOGIN_S):
    """
    Decide se é hora de verificar se a sessão/login continua ativa.

    `agora` e `ultima_checagem` devem estar na mesma base de tempo (ex:
    ambos em segundos de `time.monotonic()`). Lógica pura e testável.

    Retorna True quando:
      - `ultima_checagem is None` (nunca checou ainda); OU
      - já se passaram pelo menos `intervalo` segundos desde a última checagem.
    """
    if ultima_checagem is None:
        return True
    return (agora - ultima_checagem) >= intervalo


def url_e_do_dia(url_atual, url_esperada):
    """
    Verifica se `url_atual` ainda corresponde à página do dia (`url_esperada`).

    Usado após um refresh para garantir que a página não "voltou ao início"
    (menu de dias/evento sem data). O identificador do dia é o parâmetro
    `data=<timestamp>` presente nas URLs do BuyTicket. Lógica pura e testável.

    Regras:
      - Sem `url_atual`: retorna False (não dá para confirmar o dia).
      - Se a URL esperada tem `data=X`, exige que a atual contenha o mesmo `data=X`.
      - Se a URL esperada não tem `data=`, cai para comparação de caminho base
        (mesma origem+caminho), ignorando query.
    """
    if not url_atual:
        return False
    esperada = url_esperada or ''
    marcador = _extrair_data_param(esperada)
    if marcador is not None:
        return _extrair_data_param(url_atual) == marcador
    # Fallback: compara a parte antes da query string.
    return url_atual.split('?', 1)[0] == esperada.split('?', 1)[0]


def _extrair_data_param(url):
    """Extrai o valor do parâmetro `data=` de uma URL, ou None se ausente."""
    if not url:
        return None
    match = re.search(r'[?&]data=([^&]+)', url)
    return match.group(1) if match else None


def calcular_maior_limite(limites=None):
    """Retorna o maior limite configurado em toda a tabela."""
    limites = limites if limites is not None else LIMITES_PRECO
    return max(
        preco
        for setor in limites.values()
        for preco in setor.values()
    )


# Maior limite (usado para decisão rápida na validação inicial do botão).
MAIOR_LIMITE = calcular_maior_limite()

# ── Overrides do .env (LIMITE_<SETOR>_<CATEGORIA>=valor) ─────────────────────
# Permite ajustar limites de preço sem editar o código-fonte.
# Basta descomentar e preencher as variáveis no .env (ou .env.example).
#
# Exemplos:
#   LIMITE_PISTA_INTEIRA=5000
#   LIMITE_ARQUIBANCADA_INTEIRA=600
#   LIMITE_ARQUIBANCADA_MEIA=500
#
def _aplicar_limites_env(limites):
    """
    Sobrepõe LIMITES_PRECO com variáveis LIMITE_<SETOR>_<CAT> do .env.
    Setores compostos usam underscore: LIMITE_CADEIRA_INFERIOR_INTEIRA.
    """
    for setor, cats in limites.items():
        for cat in list(cats.keys()):
            env_key = f'LIMITE_{setor.upper()}_{cat.upper()}'
            val = os.getenv(env_key)
            if val:
                try:
                    cats[cat] = int(val)
                except ValueError:
                    pass
    return limites

LIMITES_PRECO = _aplicar_limites_env(LIMITES_PRECO)
MAIOR_LIMITE  = calcular_maior_limite(LIMITES_PRECO)  # recalcula após overrides do .env

# ============================================================
# MAPEAMENTO E REGRA DE EXCLUSÃO
# ============================================================

# Mapeamento de nomes de setor exibidos na tela -> chave interna.
# Setores NÃO listados aqui são EXCLUÍDOS automaticamente pelo filtro
# (regra de exclusão: None = ignorado). Camarotes não são mapeados
# intencionalmente — não são alvo de compra.
#
# Setores disponíveis no site (set/2026) — SEM LIMITE DE PREÇO:
#   ✅ Soundcheck             → PRIORIDADE 1 (sem limite de valor)
#   ✅ Pista                  → PRIORIDADE 2 — qualquer dia (sem limite de valor)
#   ✅ Arquibancada           → monitorado (sem limite de valor)
#   ✅ Cadeira Superior       → monitorado (sem limite de valor)
#   ✅ Cadeira Inferior       → monitorado (sem limite de valor)
#   ❌ Camarote Clássico      → excluído (não é alvo)
#   ❌ Camarote Experience + Pista → excluído (não é alvo)
#   ❌ Camarote Ídolos        → excluído (não é alvo)
#   ❌ Camarote Oitta         → excluído (não é alvo)
#   ❌ Camarote Raça          → excluído (não é alvo)
#   ❌ Camarote Stadium       → excluído (não é alvo)
#
MAPA_SETOR = {
    # ── Setores monitorados (ordem de prioridade de compra) ───────────────
    'soundcheck':                  'soundcheck',   # ← PRIORIDADE 1
    'pista soundcheck':            'soundcheck',   # variante composta
    'soundcheck pista':            'soundcheck',   # variante composta
    'pista + soundcheck':          'soundcheck',   # variante composta
    'soundcheck + pista':          'soundcheck',   # variante composta
    'pista':                       'pista',         # ← PRIORIDADE 2
    'arquibancada':                'arquibancada',
    'cadeira superior':            'cadeira_superior',
    'cadeira inferior':            'cadeira_inferior',
    # ── Todos os Camarotes abaixo são AUSENTES do mapa (excluídos) ───────
    # 'camarote clássico':            None  → retorna None → ignorado
    # 'camarote experience + pista':  None  → retorna None → ignorado
    # 'camarote ídolos':              None  → retorna None → ignorado
    # 'camarote oitta':               None  → retorna None → ignorado
    # 'camarote raça':                None  → retorna None → ignorado
    # 'camarote stadium':             None  → retorna None → ignorado
}

# Mapeamento de nomes de categoria exibidos na tela -> chave interna.
# Novo layout (set/2026): múltiplos tipos de meia + inteira.
MAPA_CATEGORIA = {
    'inteira': 'inteira',
    # Meias reconhecidas — mapeadas para 'meia'
    'meia': 'meia',
    'meia estudante': 'meia',
    'meia idoso': 'meia',
    'meia pcd': 'meia',
    'meia até 21': 'meia',
    'meia jovem baixa renda': 'meia',
    'meia professor': 'meia',
    'meia pessoas tea': 'meia',
    'meia aposentado': 'meia',
    'outras meias': 'meia',
    'acompanhante pcd': 'meia',
    'meia profissional da saúde': 'meia',   # confirmada ao vivo out/2026
    'meia profissional de saude': 'meia',   # variação sem acento
}


def mapear_setor(nome):
    """
    Converte o nome exibido do setor para a chave interna.
    Regra de exclusão: retorna None se o setor não estiver previsto.
    """
    if not nome:
        return None
    return MAPA_SETOR.get(nome.strip().lower())


def mapear_categoria(nome):
    """
    Converte o nome exibido da categoria para a chave interna.
    Regra de exclusão: retorna None se a categoria não estiver prevista.
    """
    if not nome:
        return None
    return MAPA_CATEGORIA.get(nome.strip().lower())


# ============================================================
# PARSING DE PREÇO
# ============================================================


def _normalizar_valor(bruto):
    """
    Converte a parte numérica capturada (ex: '1.600', '1600', '1.600,00',
    '1600,50') no valor inteiro em reais.

    - '.' é sempre separador de milhar (removido).
    - ',' é separador decimal: a parte decimal é descartada (reais inteiros).
    """
    bruto = bruto.strip()
    # Descarta os centavos, se houver (parte após a vírgula).
    if ',' in bruto:
        bruto = bruto.split(',', 1)[0]
    # Remove separadores de milhar.
    bruto = bruto.replace('.', '')
    if not bruto.isdigit():
        return None
    return int(bruto)


def extrair_preco(texto_preco):
    """
    Extrai preço numérico de texto como "R$ 2.991", "R$\xa01.600",
    "R$ 1000" ou "R$ 1.000,00". Retorna inteiro em reais (parte inteira)
    ou None quando não há preço reconhecível.

    Nota: números de 4+ dígitos SEM separador de milhar (ex: "R$ 1000")
    são tratados corretamente como 1000 (e não truncados para 100).
    """
    if not texto_preco:
        return None
    # Normaliza espaços não-quebráveis e entidades HTML de espaço.
    texto = texto_preco.replace('\xa0', ' ').replace('&nbsp;', ' ')
    # Captura a sequência de dígitos com eventuais separadores após "R$".
    match = re.search(r'R\$\s*([\d.,]+)', texto)
    if match:
        return _normalizar_valor(match.group(1))
    # Último recurso: número com ponto de milhar sem o prefixo R$.
    match = re.search(r'(\d{1,3}(?:\.\d{3})+)', texto)
    if match:
        return _normalizar_valor(match.group(1))
    return None


# ============================================================
# VALIDAÇÃO INICIAL DO BOTÃO DE COMPRA
# ============================================================


def validar_botao_inicial(preco_botao, maior_limite=None):
    """
    Validação inicial (V1): o valor do botão está dentro dos parâmetros?

    Retorna uma tupla (ok, motivo):
      - (False, 'sem_preco')      -> botão sem preço reconhecível.
      - (False, 'fora_parametro') -> preço acima do maior limite configurado.
      - (True,  'ok')             -> preço dentro dos parâmetros; pode avançar.
    """
    limite = maior_limite if maior_limite is not None else MAIOR_LIMITE
    if preco_botao is None:
        return False, 'sem_preco'
    if preco_botao > limite:
        return False, 'fora_parametro'
    return True, 'ok'


# ============================================================
# FILTRAGEM E PRIORIZAÇÃO DE TIPOS (SETORES)
# ============================================================


def filtrar_setores_validos(setores, limites=None):
    """
    Filtra e ordena os setores disponíveis.

    `setores`: lista de dicts com pelo menos {'nome': str, 'preco_min': int}.
      Pode conter chaves extras (ex: 'elemento' com o WebElement).

    Regras aplicadas:
      1. Regra de exclusão: setores não mapeados são ignorados.
      2. Filtragem: mantém apenas setores cujo preço mínimo <= maior limite
         do próprio setor (pode caber em meia ou inteira).
      3. Priorização: ordena por PRIORIDADE_SETOR (Soundcheck primeiro, depois Pista).

    Retorna nova lista de dicts, cada um com 'key' adicionada, na ordem de
    tentativa desejada.
    """
    limites = limites if limites is not None else LIMITES_PRECO
    validos = []
    for setor in setores:
        nome = setor.get('nome')
        preco_min = setor.get('preco_min')
        key = mapear_setor(nome)
        # Regra de exclusão: ignora setor não mapeado.
        if key is None:
            continue
        # Ignora setor sem preço reconhecível.
        if preco_min is None:
            continue
        limites_setor = limites.get(key, {})
        if not limites_setor:
            continue
        maior_limite_setor = max(limites_setor.values())
        if preco_min <= maior_limite_setor:
            item = dict(setor)
            item['key'] = key
            validos.append(item)
    validos.sort(key=lambda s: PRIORIDADE_SETOR.get(s['key'], 99))
    return validos


# ============================================================
# FILTRAGEM DE CATEGORIAS
# ============================================================


def filtrar_categorias_validas(categorias, setor_key, limites=None):
    """
    Filtra as categorias disponíveis para um setor.

    `categorias`: lista de dicts com pelo menos {'nome': str, 'preco': int|None}.
    `setor_key`: chave interna do setor selecionado.

    Novo layout (set/2026): o preço pode não estar disponível na opção do
    ComboBox (aparece como None ou 0) — nesses casos, a categoria é incluída
    como "a verificar" e o preço real é lido do botão Comprar após a seleção.

    Regras aplicadas:
      1. Regra de exclusão: categorias não mapeadas são ignoradas.
      2. Se preço disponível e > 0: valida contra o limite. Se acima, exclui.
      3. Se preço indisponível (None ou 0): inclui como candidata (preço será
         verificado via botão após seleção no DOM).

    Retorna lista de dicts (com 'key' e 'limite' adicionados) que atendem aos
    critérios, ordenada por preço (menores primeiro; None/0 por último).
    """
    limites = limites if limites is not None else LIMITES_PRECO
    limites_setor = limites.get(setor_key, {})
    validas = []
    for cat in categorias:
        nome = cat.get('nome')
        preco = cat.get('preco')
        key = mapear_categoria(nome)
        # Regra de exclusão: ignora categoria não mapeada.
        if key is None:
            continue
        limite = limites_setor.get(key)
        if limite is None:
            continue
        # Ignora categorias sem preço ou com preço <= 0.
        # Preço disponível: valida contra o limite.
        if preco is not None and preco > 0:
            if preco > limite:
                # Para 'inteira': incluir mesmo com preço aparente acima do limite
                # O preço real só é confirmado via botão após seleção no DOM.
                # Para 'meia': excluir se preço confirmado acima do limite.
                if key != 'inteira':
                    continue  # exclui meia acima do limite
                # Inteira com preço aparente acima: inclui para verificar via botão
        # Preço indisponível (None ou 0): inclui como candidata.
        # O preço real será verificado pelo bot após selecionar no DOM.
        item = dict(cat)
        item['key'] = key
        item['limite'] = limite
        validas.append(item)

    # Ordena: preço conhecido (menor primeiro), depois preço desconhecido
    validas.sort(key=lambda c: (c.get('preco') is None or c.get('preco', 0) == 0,
                                c.get('preco') or 999999))
    return validas


# ============================================================
# VALIDAÇÃO FINAL ESTRITA
# ============================================================


def validar_valor_final(preco_botao, preco_categoria):
    """
    Validação final (antes do clique de COMPRAR).

    Novo layout (set/2026): o preço da categoria pode ser None quando não
    estava disponível no ComboBox — nesse caso usa o preço do botão como
    referência direta (sempre ok, desde que não seja None).

    Retorna (ok, motivo):
      - (False, 'sem_preco')  -> não foi possível ler o preço do botão.
      - (False, 'divergente') -> valores diferentes (quando ambos conhecidos).
      - (True,  'ok')         -> pode clicar em comprar.
    """
    if preco_botao is None:
        return False, 'sem_preco'
    # Preço da categoria desconhecido: usa o botão como referência — ok
    if preco_categoria is None or preco_categoria == 0:
        return True, 'ok'
    if preco_botao != preco_categoria:
        return False, 'divergente'
    return True, 'ok'


# ============================================================
# TELEMETRIA — Cronometro
# ============================================================

class Cronometro:
    """
    Context manager de telemetria por etapa do fluxo. Thread-safe.

    Registra a duração de cada bloco nomeado e permite consultar
    estatísticas (mín/méd/máx) por etapa via Cronometro.relatorio().

    Uso:
        with Cronometro('login'):
            ...  # código a medir

    As medições são acumuladas em _registro (dict global da classe)
    e podem ser resetadas com Cronometro.resetar().
    """

    _lock: threading.Lock = threading.Lock()
    _registro: dict = {}  # etapa -> list[float]

    def __init__(self, etapa: str):
        self._etapa = etapa
        self._ini: float = 0.0

    def __enter__(self) -> 'Cronometro':
        self._ini = _time.monotonic()
        return self

    def __exit__(self, *_) -> None:
        dur = _time.monotonic() - self._ini
        with Cronometro._lock:
            Cronometro._registro.setdefault(self._etapa, []).append(dur)

    @classmethod
    def relatorio(cls) -> str:
        """Formata tabela de min/méd/máx por etapa. Marca gargalos (méd>5s ou máx>15s)."""
        linhas = [
            '=' * 70,
            f'{"RELATÓRIO DE TELEMETRIA":^70}',
            '=' * 70,
            f'{"Etapa":<28} {"Mín(s)":>7} {"Méd(s)":>7} {"Máx(s)":>7} {"N":>4}  Gargalo',
            '-' * 70,
        ]
        with cls._lock:
            reg = dict(cls._registro)

        if not reg:
            linhas.append('  (sem medições registradas)')
        else:
            total_medio = 0.0
            for etapa, vals in sorted(reg.items()):
                mi = min(vals)
                me = sum(vals) / len(vals)
                ma = max(vals)
                total_medio += me
                gargalo = '⚠️' if me > 5 or ma > 15 else ''
                linhas.append(
                    f'{etapa:<28} {mi:>7.2f} {me:>7.2f} {ma:>7.2f} {len(vals):>4}  {gargalo}'
                )
            linhas.append('-' * 70)
            linhas.append(f'Total médio estimado por execução: {total_medio:.2f}s')
            linhas.append('Gargalo ⚠️ = méd > 5s ou máx > 15s')

        linhas.append('=' * 70)
        return '\n'.join(linhas)

    @classmethod
    def resetar(cls) -> None:
        """Limpa todos os registros acumulados."""
        with cls._lock:
            cls._registro.clear()
