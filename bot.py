"""
Bot de Monitoramento e Compra Automática de Ingressos BTS 2026
Monitora BuyTicket e Ticketmaster, tenta comprar quando dentro do limite de preço.
Envia notificações via ntfy.sh a cada etapa.

Uso: py bot.py

Otimizações aplicadas (Tasks 2-5):
  - Flags de velocidade no Chrome + bloqueio CDP de recursos não essenciais
  - Waits explícitos orientados a estado (WebDriverWait/EC) em vez de sleeps fixos
  - Utilitários _wait_for / _wait_until_gone / _wait_for_any
  - MAX_RETRIES=3 padronizado em todas as etapas críticas
  - _btn_por_texto() substitui XPaths complexos por filtragem Python pura
  - Cronometro instrumenta cada etapa para telemetria de latência
"""

import json
import os
import re
import random
import socket
import time
import ctypes
import threading

# ── Lock global de compra ─────────────────────────────────────────────────────
# Garante que apenas UMA thread por vez executa o fluxo de compra (selecionar +
# comprar + checkout). As demais ficam monitorando preços mas aguardam a vez.
# Evita "compra em andamento" causada por 3 threads comprando simultaneamente.
_COMPRA_LOCK = threading.Lock()
import traceback
from datetime import datetime

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    InvalidSessionIdException,
    TimeoutException,
    NoSuchElementException,
)
from webdriver_manager.chrome import ChromeDriverManager
import undetected_chromedriver as uc

# Lógica pura (testável sem navegador): limites, parsing, mapeamento,
# filtragem, priorização, validações e telemetria.
import ticket_logic
from ticket_logic import (
    LIMITES_PRECO,
    MAIOR_LIMITE,
    MAPA_SETOR,
    MAPA_CATEGORIA,
    MAX_CICLOS,
    MAX_RETRIES,
    INTERVALO_CHECK_LOGIN_S,
    Cronometro,
    extrair_preco,
    validar_botao_inicial,
    filtrar_setores_validos,
    filtrar_categorias_validas,
    validar_valor_final,
    deve_checar_login,
    url_e_do_dia,
)

# ============================================================
# CONFIGURAÇÃO
# ============================================================

load_dotenv()

# ── Credenciais BuyTicket ─────────────────────────────────────────────────────
# Novo login (set/2026): CPF na etapa 1, senha na etapa 2.
# BUYTICKET_CPF é o identificador principal; BUYTICKET_EMAIL mantido como
# fallback caso a variável antiga ainda exista no .env do usuário.
BUYTICKET_CPF   = os.getenv('BUYTICKET_CPF') or os.getenv('CPF', '')
BUYTICKET_EMAIL = os.getenv('BUYTICKET_EMAIL')   # legado — não mais usado no login
BUYTICKET_SENHA = os.getenv('BUYTICKET_SENHA')

# ── Credenciais Ticketmaster ──────────────────────────────────────────────────
TICKETMASTER_EMAIL = os.getenv('TICKETMASTER_EMAIL')
TICKETMASTER_SENHA = os.getenv('TICKETMASTER_SENHA')

# Dados pessoais
NOME_COMPLETO = os.getenv('NOME_COMPLETO')
CPF = os.getenv('CPF')
TELEFONE = os.getenv('TELEFONE')
CEP = os.getenv('CEP')
ESTADO = os.getenv('ESTADO')
BAIRRO = os.getenv('BAIRRO')
CIDADE = os.getenv('CIDADE')
ENDERECO = os.getenv('ENDERECO')
NUMERO = os.getenv('NUMERO')
COMPLEMENTO = os.getenv('COMPLEMENTO', '')
EMAIL = os.getenv('EMAIL')

# Notificação
NTFY_TOPIC    = os.getenv('NTFY_TOPIC')       # celular — todas as notificações
NTFY_TOPIC_PC = os.getenv('NTFY_TOPIC_PC', '') # notebook — somente compra iniciada e dados preenchidos

# Monitoramento
INTERVALO_CHECK = int(os.getenv('INTERVALO_CHECK_MS', '3000')) / 1000  # em segundos
HEADLESS = os.getenv('HEADLESS', 'false').lower() == 'true'
MINIMIZED = os.getenv('MINIMIZED', 'true').lower() == 'true'
START_MINIMIZED = os.getenv('START_MINIMIZED', 'true').lower() == 'true'

# Padrão de tempo para verificar se a sessão/login caiu (em segundos).
INTERVALO_LOGIN = int(os.getenv('INTERVALO_CHECK_LOGIN_S', str(INTERVALO_CHECK_LOGIN_S)))

# Modo de validação: DRY_RUN=true não clica em comprar nem executa checkout/PIX.
COMPRA_REAL = os.getenv('COMPRA_REAL', 'false').lower() == 'true'
DRY_RUN = os.getenv('DRY_RUN', str(not COMPRA_REAL)).lower() == 'true'

# Bloqueio CDP de recursos não essenciais (analytics, fonts, trackers).
CDP_BLOQUEIO = os.getenv('CDP_BLOQUEIO', 'true').lower() == 'true'

# ── URLs BuyTicket ────────────────────────────────────────────────────────────
# URL base do evento (sem sessão) — usado como ponto de entrada inicial.
URL_EVENTO_BASE  = os.getenv('BUYTICKET_URL_BASE', 'https://buyticketbrasil.com/event/bts-2026worldtourarirang')
BUYTICKET_LOGIN_URL = os.getenv('BUYTICKET_LOGIN_URL', 'https://buyticketbrasil.com/entrar')

# URLs de sessão por dia — override via BUYTICKET_URLS no .env (vírgula-separado).
# Fallback: URLs confirmadas em set/2026 para o evento BTS Morumbis.
_bt_urls_env = os.getenv('BUYTICKET_URLS', '')
URLS_BUYTICKET = [u.strip() for u in _bt_urls_env.split(',') if u.strip()] or [
    'https://buyticketbrasil.com/event/bts-2026worldtourarirang/session/e4ac9384-ccd7-4f29-82b8-7ba8677bc2ed',  # 28/10
    'https://buyticketbrasil.com/event/bts-2026worldtourarirang/session/ced30381-0cc4-4779-9c6b-ba05f44d086e',  # 30/10
    'https://buyticketbrasil.com/event/bts-2026worldtourarirang/session/7dbb3a2b-985d-4bf2-9d36-df893169e098',  # 31/10
]

# ── URLs Ticketmaster ─────────────────────────────────────────────────────────
TICKETMASTER_URL_HOME = os.getenv('TICKETMASTER_URL_HOME', 'https://www.ticketmaster.com.br/')
URL_TM_HOME = TICKETMASTER_URL_HOME   # alias usado internamente

_tm_urls_env = os.getenv('TICKETMASTER_URLS', '')
URLS_TICKETMASTER = [u.strip() for u in _tm_urls_env.split(',') if u.strip()] or [
    'https://www.ticketmaster.com.br/event/venda-geral-bts-world-tour-arirang-28-10',
    'https://www.ticketmaster.com.br/event/venda-geral-bts-world-tour-arirang-30-10',
    'https://www.ticketmaster.com.br/event/venda-geral-bts-world-tour-arirang-31-10',
]

# Datas dos shows — override via DATAS_SHOW no .env (vírgula-separado).
_datas_env = os.getenv('DATAS_SHOW', '')
DATAS_SHOW = [d.strip() for d in _datas_env.split(',') if d.strip()] or ['28/10', '30/10', '31/10']

# Modo de monitoramento: 'ambos' (padrão), 'buyticket' ou 'ticketmaster'.
# Defina MONITOR_ONLY=ticketmaster no .env para desativar o monitor BuyTicket.
MONITOR_ONLY        = os.getenv('MONITOR_ONLY', 'ambos').lower().strip()
ATIVAR_BUYTICKET    = MONITOR_ONLY in ('ambos', 'buyticket')
ATIVAR_TICKETMASTER = MONITOR_ONLY in ('ambos', 'ticketmaster')

# Modo monitor sem login: ativa automaticamente quando BUYTICKET_CPF ou
# BUYTICKET_SENHA não estão configurados. O bot monitora preços e envia
# notificações, mas NÃO tenta comprar (sem sessão autenticada).
# Para ativar compra automática: preencha BUYTICKET_CPF e BUYTICKET_SENHA no .env.
BUYTICKET_MODO_MONITOR = not (BUYTICKET_CPF and BUYTICKET_SENHA)

# ChromeDriver path (baixado uma vez antes das threads)
CHROMEDRIVER_PATH = None

# ============================================================
# CDP — padrões de bloqueio de recursos não essenciais
# ============================================================
# Não afetam o fluxo de compra. Reduzem payload e latência de cada navegação.

_PADROES_BLOQUEIO_CDP = [
    '*.google-analytics.com*',
    '*.googletagmanager.com*',
    '*.facebook.net*',
    '*.hotjar.com*',
    '*.intercom.io*',
    'fonts.googleapis.com*',
    'fonts.gstatic.com*',
    '*.doubleclick.net*',
    '*.clarity.ms*',
    '*.zendesk.com*',
    '*.zdassets.com*',
]


def _ativar_bloqueio_cdp(driver):
    """
    Ativa bloqueio de recursos não essenciais via CDP Network.

    Bloqueia analytics, trackers, fontes externas e scripts de chat ao vivo
    que não afetam o fluxo de compra, reduzindo payload e latência de
    cada navegação. Falha silenciosamente se o CDP não estiver disponível.
    """
    if not CDP_BLOQUEIO:
        return
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        driver.execute_cdp_cmd('Network.setBlockedURLs', {'urls': _PADROES_BLOQUEIO_CDP})
        log(f'CDP bloqueio ativado: {len(_PADROES_BLOQUEIO_CDP)} padroes', 'BOT')
    except Exception as e:
        log(f'CDP bloqueio: aviso ({e}). Prosseguindo sem bloqueio.', 'BOT')


# ============================================================
# UTILIDADES — LOG, NOTIFICAÇÃO, SCREENSHOT
# ============================================================


def log(msg, prefix='BOT'):
    """Log com timestamp; imprime no console e grava em arquivo (bot.log)."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    linha = f'[{ts}] [{prefix}] {msg}'
    print(linha)
    try:
        with open('bot.log', 'a', encoding='utf-8') as _f:
            _f.write(linha + chr(10))
    except Exception:
        pass


# ── Orçamento diário de notificações (ntfy.sh free ≈ 250/dia) ────────────────
# Limite conservador: 200/dia.  Ao passar de 50% → envia só "importantes";
# ao passar de 90% → envia só "críticas".  Reset automático à meia-noite.
NTFY_LIMITE_DIARIO = 200

_ntfy_lock       = __import__('threading').Lock()
_ntfy_timestamps : list = []                       # controle de burst por segundo
_ntfy_contador   = {'data': None, 'total': 0}      # contador diário


def _ntfy_nivel(titulo: str, prioridade: str) -> int:
    """
    Classifica cada mensagem em 3 níveis de importância:
      1 – CRÍTICA   : PIX gerado/expirando, compra concluída, bloqueio
      2 – IMPORTANTE: tentativa de compra, ingresso encontrado, fila, erros
      3 – NORMAL    : login, status de monitor, mensagens de rotina
    """
    t = titulo.upper()
    if prioridade in ('urgent', 'high'):
        return 1
    criticos = ('PIX GERADO', 'CODIGO PIX', 'PIX EXPIRANDO', 'PAGUE AGORA',
                'COMPRA CONCLUIDA', 'PAGAMENTO CONFIRMADO',
                'SITE ERRADO', 'ACESSO BLOQUEADO')
    if any(c in t for c in criticos):
        return 1
    importantes = ('COMPRANDO', 'INGRESSO!', 'FILA LIBERADA', 'PIX EXPIROU',
                   'TIMEOUT PIX', 'PIX NAO GERADO', 'ERRO NO CHECKOUT',
                   'BUYTICKET SESSAO', 'BOT ENCERRADO', 'BOT NAO INICIADO')
    if any(i in t for i in importantes):
        return 2
    return 3   # tudo o mais: status routine, login, monitor iniciado, etc.


def notificar(titulo, mensagem='', prioridade='default'):
    """
    Envia notificação push via ntfy.sh em thread separada (não bloqueia o bot).

    Orçamento diário (ntfy.sh free ≈ 250/dia — limite conservador: 200):
      < 50 %  → envia tudo            (nível 1, 2 e 3)
      50-89 % → envia só importantes   (nível 1 e 2)
      >= 90 % → envia só críticas      (nível 1)
      Esgotado → somente nível 1 (sem incrementar o contador)

    Rate-limit por burst: máx 2 mensagens não-críticas a cada 10 s.
    Contador reseta automaticamente à meia-noite.
    """
    import threading, datetime

    nivel_msg = _ntfy_nivel(titulo, prioridade)

    # Fase 1: decidir se a mensagem deve ser enviada agora
    hoje = datetime.date.today().isoformat()
    with _ntfy_lock:
        if _ntfy_contador['data'] != hoje:
            _ntfy_contador['data'] = hoje
            _ntfy_contador['total'] = 0
            log('NTFY: contador diario resetado (novo dia).', 'NTFY')

        usado = _ntfy_contador['total']
        pct   = usado / NTFY_LIMITE_DIARIO

        # Nivel minimo permitido baseado no uso do orcamento
        if   pct >= 0.90: nivel_min = 1
        elif pct >= 0.50: nivel_min = 2
        else:             nivel_min = 3

        # Descarta se menos importante que o minimo permitido
        if nivel_msg > nivel_min:
            return

        # Esgotado mas mensagem critica: passa sem incrementar o contador
        if usado >= NTFY_LIMITE_DIARIO:
            if nivel_msg > 1:
                log(f'NTFY: orcamento esgotado — descartado: {titulo}', 'NTFY')
                return
            # nivel 1 passa mesmo esgotado, sem contar
        else:
            _ntfy_contador['total'] += 1
            usado = _ntfy_contador['total']

        # Avisos ao cruzar os limiares (logado uma unica vez por limiar)
        if usado == int(NTFY_LIMITE_DIARIO * 0.50):
            log(f'NTFY: 50% do orcamento usado ({usado}/{NTFY_LIMITE_DIARIO}). '
                f'Somente mensagens importantes daqui em diante.', 'NTFY')
        elif usado == int(NTFY_LIMITE_DIARIO * 0.90):
            log(f'NTFY: 90% do orcamento usado ({usado}/{NTFY_LIMITE_DIARIO}). '
                f'Somente mensagens criticas!', 'NTFY')

    snapshot_usado = usado  # captura para o log dentro da thread

    # Fase 2: enviar em background
    def _enviar():
        agora = time.monotonic()

        # Burst limiter: nao-criticas (nivel 2-3) max 2 em 10 s
        if nivel_msg > 1:
            with _ntfy_lock:
                recentes = [t for t in _ntfy_timestamps if agora - t < 10]
                if len(recentes) >= 2:
                    return
                _ntfy_timestamps[:] = recentes + [agora]

        for tentativa in range(3):
            try:
                titulo_ascii = titulo.encode('ascii', 'replace').decode('ascii')
                r = requests.post(
                    f'https://ntfy.sh/{NTFY_TOPIC}',
                    data=mensagem.encode('utf-8'),
                    headers={'Title': titulo_ascii, 'Priority': prioridade},
                    timeout=5,
                )
                if r.status_code < 300:
                    log(f'Notificacao [{snapshot_usado}/{NTFY_LIMITE_DIARIO}] '
                        f'nv{nivel_msg}: {titulo}', 'NTFY')
                    return
                elif r.status_code == 429:
                    log(f'Rate limit ntfy (429): {titulo}', 'NTFY')
                    return
                else:
                    log(f'Notificacao HTTP {r.status_code}: {titulo}', 'NTFY')
                    return
            except Exception as e:
                if tentativa < 2:
                    time.sleep(2)
                else:
                    log(f'Erro ao notificar ({titulo}): {type(e).__name__}', 'NTFY')

    threading.Thread(target=_enviar, daemon=True).start()


def notificar_pc(titulo, mensagem='', prioridade='default'):
    """
    Notificação exclusiva para o notebook via ntfy.sh (NTFY_TOPIC_PC).

    Disparada somente em dois momentos de alta importância:
      • compra iniciada (lock adquirido, checkout começando)
      • dados pessoais sendo preenchidos no checkout

    Sem rate-limit nem controle de orçamento — no máximo 2-3 mensagens
    por tentativa de compra, logo não há risco de esgotar cotas.
    Se NTFY_TOPIC_PC não estiver configurado, retorna silenciosamente.
    """
    if not NTFY_TOPIC_PC:
        return

    import threading

    def _enviar():
        for tentativa in range(3):
            try:
                titulo_ascii = titulo.encode('ascii', 'replace').decode('ascii')
                r = requests.post(
                    f'https://ntfy.sh/{NTFY_TOPIC_PC}',
                    data=mensagem.encode('utf-8'),
                    headers={'Title': titulo_ascii, 'Priority': prioridade},
                    timeout=5,
                )
                if r.status_code < 300:
                    log(f'PC notif: {titulo}', 'NTFY')
                    return
                elif r.status_code == 429:
                    log(f'PC notif rate limit (429): {titulo}', 'NTFY')
                    return
                else:
                    log(f'PC notif HTTP {r.status_code}: {titulo}', 'NTFY')
                    return
            except Exception as e:
                if tentativa < 2:
                    time.sleep(2)
                else:
                    log(f'Erro PC notif ({titulo}): {type(e).__name__}', 'NTFY')

    threading.Thread(target=_enviar, daemon=True).start()


def screenshot(driver, nome):
    """Salva screenshot para debug."""
    try:
        ts = int(time.time())
        path = f'screenshots/{nome}_{ts}.png'
        os.makedirs('screenshots', exist_ok=True)
        driver.save_screenshot(path)
        log(f'Screenshot: {path}')
    except Exception:
        pass


# ============================================================
# UTILIDADES — WAITS ORIENTADOS A ESTADO (substituem sleeps fixos)
# ============================================================

def _wait_for(driver, selector, timeout=8, by=By.CSS_SELECTOR):
    """
    Aguarda até `timeout` segundos que o elemento identificado por `selector`
    fique visível no DOM. Retorna o elemento ou None em caso de timeout.

    Substitui os padrões: driver.get(url) + time.sleep(N) — em vez de esperar
    um tempo fixo, retorna assim que o elemento-âncora aparecer.
    """
    try:
        return WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((by, selector))
        )
    except TimeoutException:
        log(f'_wait_for timeout ({timeout}s): {selector}', 'WAIT')
        return None
    except Exception as e:
        log(f'_wait_for erro: {selector} — {e}', 'WAIT')
        return None


def _wait_until_gone(driver, selector, timeout=8, by=By.CSS_SELECTOR):
    """
    Aguarda até `timeout` segundos que o elemento identificado por `selector`
    desapareça do DOM (invisível ou removido). Retorna True se sumiu.

    Substitui os padrões: btn.click() + time.sleep(N) esperando um overlay
    desaparecer ou uma tela mudar.
    """
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((by, selector))
        )
        return True
    except TimeoutException:
        return False
    except Exception:
        return True  # elemento provavelmente não existe, considera como sumido


def _wait_for_any(driver, selectors, timeout=8):
    """
    Aguarda até `timeout` segundos que QUALQUER seletor CSS da lista fique
    visível. Retorna o seletor que apareceu primeiro, ou None em timeout.

    Útil para aguardar um conjunto de estados possíveis após uma ação
    (ex: após clicar COMPRAR pode aparecer: modal de segurança, fila ou
    tela de checkout).
    """
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        for sel in selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and els[0].is_displayed():
                    return sel
            except Exception:
                pass
        time.sleep(0.08)
    return None


def _wait_for_url_change(driver, url_original, timeout=8):
    """
    Aguarda até `timeout` segundos que a URL mude em relação a `url_original`.
    Retorna True se mudou, False em timeout.
    """
    try:
        WebDriverWait(driver, timeout).until(
            EC.url_changes(url_original)
        )
        return True
    except TimeoutException:
        return False
    except Exception:
        return False


# Seletor âncora: confirma que a SPA do evento/sessão renderizou.
# Atualizado set/2026: novo layout usa ComboBox (input[role="combobox"]) e
# PriceActionButton na página /event/slug/session/UUID.
_SEL_ANCORA_EVENTO = (
    'input[role="combobox"],'                       # ✅ novo: campos Tipo/Categoria
    'button[class*="PriceActionButton"],'           # ✅ novo: botão Comprar com preço
    'button[class*="SessionDateRow"],'              # ✅ novo: botões de data na página-mãe
    'button[class*="dropdownHead"]'                 # legado: mantido como fallback
)

# Domínio esperado para todas as abas de monitoramento BuyTicket.
_DOMINIO_BUYTICKET = os.getenv('BUYTICKET_DOMINIO', 'buyticketbrasil.com')

# ── Seletores confirmados do novo layout (set/2026) ───────────────────────────
#
# Página-mãe: /event/bts-2026worldtourarirang
#   Botão de dia/show: button[class*="SessionDateRow"]
#   Identificar o dia pelo texto do botão (dia + mês)
#
# Página de sessão: /event/bts-2026worldtourarirang/session/<UUID>
#   Dropdown Tipo de Ingresso: input[role="combobox"][placeholder="Selecione o tipo de ingresso"]
#   Dropdown Categoria:        input[role="combobox"][placeholder="Selecione a categoria"]
#   Botão comprar (com preço): button[class*="PriceActionButton"] (texto "R$ X\nmenor preço\nComprar")
#
# Listbox de opções (quando dropdown aberto):
#   A inspeção pendente confirmará; provável: ul[role="listbox"] ou div[class*="ComboBox-module...list"]
#
_SEL_COMBOBOX_TIPO      = 'input[role="combobox"][placeholder*="tipo"]'
_SEL_COMBOBOX_CATEGORIA = 'input[role="combobox"][placeholder*="categoria"]'
_SEL_BTN_COMPRAR        = 'button[class*="PriceActionButton"]'
_SEL_BTN_DATA           = 'button[class*="SessionDateRow"]'

# Texto identificador de cada dia (sem Camarote = ingresso geral)
_TEXTO_DIA = {
    '28/10': '28',
    '30/10': '30',
    '31/10': '31',
}


def _verificar_site_correto(driver, data_show=''):
    """
    Verifica se o driver está numa aba do BuyTicket.
    Se detectar que a URL é de outro site (ex: Ticketmaster), emite alerta
    imediato via notificação push e salva screenshot.

    Retorna True se o site está correto, False se está no site errado.
    Deve ser chamada a cada ciclo no _monitor_dia para detecção precoce.
    """
    try:
        url_atual = (driver.current_url or '').lower()
        if not url_atual or url_atual == 'about:blank':
            return True  # aba ainda abrindo — não é erro

        if _DOMINIO_BUYTICKET in url_atual:
            return True  # tudo certo

        # Site errado detectado — alerta urgente
        prefixo = f'[{data_show}] ' if data_show else ''
        msg = (
            f'{prefixo}ALERTA: automação está no site ERRADO!\n'
            f'URL atual: {driver.current_url}\n'
            f'Esperado: {_DOMINIO_BUYTICKET}\n'
            f'Verifique imediatamente — nenhuma compra foi feita.'
        )
        log(msg, 'BT')
        notificar('⚠️ SITE ERRADO DETECTADO', msg, 'urgent')
        screenshot(driver, f'site_errado_{data_show.replace("/", "") if data_show else "unk"}')
        return False

    except Exception as e:
        log(f'Erro ao verificar site: {e}', 'BT')
        return True  # na dúvida não bloqueia

# Seletor que confirma que o usuário está logado (avatar com iniciais).
# Fallback: qualquer input desabilitado da conta logada.
_SEL_LOGADO = 'button[class*="avatarBtn"], button[class*="userBtn"], [class*="userAvatar"]'


# ============================================================
# UTILIDADES — INTERAÇÃO COM BOTÕES (substituem XPaths complexos)
# ============================================================

def _btn_por_texto(driver, texto, apenas_visivel=True):
    """
    Retorna todos os botões cujo texto normalizado contém `texto` (case-insensitive).

    Substitui os XPaths `//button[contains(normalize-space(.), 'Texto')]` por
    filtragem Python pura sobre find_elements CSS, que é mais rápido e não
    depende do motor XPath do WebDriver.

    Se `apenas_visivel=True` (padrão), filtra apenas os que estão visíveis.
    """
    alvo = texto.strip().lower()
    try:
        todos = driver.find_elements(By.CSS_SELECTOR, 'button')
        candidatos = [
            b for b in todos
            if alvo in (b.text or '').strip().lower()
        ]
        if apenas_visivel:
            candidatos = [b for b in candidatos if b.is_displayed()]
        return candidatos
    except Exception:
        return []


def _clique_resiliente(driver, el):
    """Clica no elemento; se interceptado por overlay, tenta via JavaScript."""
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script('arguments[0].click();', el)
            return True
        except Exception:
            return False


# ============================================================
# CRIAÇÃO DO DRIVER (Task 2: flags de velocidade + CDP)
# ============================================================


def criar_driver():
    """Cria instancia do Chrome WebDriver com flags de velocidade."""
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--incognito")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--no-first-run")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    usar_offscreen = MINIMIZED and not HEADLESS and not START_MINIMIZED
    if usar_offscreen:
        options.add_argument("--window-position=-32000,-32000")

    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(0)

    if START_MINIMIZED and not HEADLESS:
        try:
            driver.minimize_window()
        except Exception:
            pass

    _ativar_bloqueio_cdp(driver)
    return driver


def _fechar_popup_aviso(driver):
    """
    Fecha o pop-up "Estamos ajustando a plataforma" se estiver visível.
    Aparece intermitentemente após atualizações do site (set/2026+).
    Seletor: button[type='button'] com texto 'Entendi'.
    """
    try:
        btns = driver.find_elements(By.XPATH, "//button[contains(normalize-space(.), 'Entendi')]")
        for btn in btns:
            if btn.is_displayed():
                btn.click()
                log('Pop-up de aviso fechado ("Entendi")', 'BT')
                time.sleep(0.15)
                return True
    except Exception:
        pass
    return False


def buyticket_login(driver):
    """
    Faz login no BuyTicket em uma nova aba separada.
    Depois fecha a aba e volta para a aba original.
    Tenta até MAX_RETRIES vezes.

    Novo fluxo (set/2026) — 2 etapas:
      1. Etapa CPF: input[autocomplete='username'] + button[type='submit'] "Continuar"
         → URL muda para /login
      2. Etapa Senha: input[type='password'] + button[type='submit'] "Entrar"
         → URL muda para / (home)

    Pop-up intermitente "Estamos ajustando a plataforma" fechado via _fechar_popup_aviso().
    """
    log('Iniciando login BuyTicket (novo fluxo CPF/2 etapas)...', 'BT')
    notificar('BuyTicket Login', 'Fazendo login no BuyTicket...')

    aba_original = driver.current_window_handle

    for tentativa in range(MAX_RETRIES):
        # Abre nova aba SOMENTE se há mais de 1 aba abertas (há abas de evento ativas).
        # Em sessão nova (1 só aba em branco), navega direto para não perder o contexto.
        abas_antes = driver.window_handles
        if len(abas_antes) > 1:
            driver.execute_script("window.open('')")
            abas = driver.window_handles
            driver.switch_to.window(abas[-1])
            abriu_nova_aba = True
        else:
            # Sessão nova: usa a aba atual em branco para o login
            abriu_nova_aba = False

        try:
            with Cronometro('login'):
                driver.get(BUYTICKET_LOGIN_URL)
                # Sem wait_for(body) aqui — o wait do campo CPF já cobre o carregamento

                # ── Fecha pop-up de aviso (pode aparecer por cima do campo CPF) ─
                _fechar_popup_aviso(driver)
                time.sleep(0.1)
                _fechar_popup_aviso(driver)  # segunda tentativa — pop-up pode reaparecer

                # ── ETAPA 1: CPF ──────────────────────────────────────────────
                campo_cpf = _wait_for(
                    driver,
                    'input[autocomplete="username"]',
                    timeout=15,
                )
                if not campo_cpf:
                    log(f'Login: campo CPF nao apareceu (tentativa {tentativa+1}/{MAX_RETRIES})', 'BT')
                    screenshot(driver, f'login_sem_cpf_{tentativa+1}')
                    driver.close()
                    driver.switch_to.window(aba_original)
                    continue

                campo_cpf.clear()
                campo_cpf.send_keys(BUYTICKET_CPF)

                # Botão "Continuar" — button[type='submit'] na etapa 1
                btn_continuar = driver.find_elements(
                    By.CSS_SELECTOR, "button[type='submit']"
                )
                if not btn_continuar:
                    log(f'Login: botao Continuar nao encontrado (tentativa {tentativa+1}/{MAX_RETRIES})', 'BT')
                    driver.close()
                    driver.switch_to.window(aba_original)
                    continue

                url_etapa1 = driver.current_url
                btn_continuar[0].click()

                # Aguarda transição para a etapa da senha (/login)
                _wait_for_url_change(driver, url_etapa1, timeout=8)

                # ── ETAPA 2: Senha ───────────────────────────────────────────
                # Seletor confirmado: input[type='password'][autocomplete='current-password']
                campo_senha = _wait_for(
                    driver,
                    'input[type="password"][autocomplete="current-password"]',
                    timeout=10,
                )
                if not campo_senha:
                    # Fallback: qualquer input de senha
                    els = driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
                    campo_senha = els[0] if els else None

                if not campo_senha:
                    log(f'Login: campo senha nao encontrado (tentativa {tentativa+1}/{MAX_RETRIES})', 'BT')
                    screenshot(driver, f'login_sem_senha_{tentativa+1}')
                    driver.close()
                    driver.switch_to.window(aba_original)
                    continue

                campo_senha.clear()
                campo_senha.send_keys(BUYTICKET_SENHA)

                url_etapa2 = driver.current_url

                # Botão "Entrar" — button[type='submit'] na etapa 2
                # ENTER no campo também funciona e é mais robusto
                campo_senha.send_keys(Keys.ENTER)

                # Aguarda URL mudar para fora de /login (confirma login OK)
                _wait_for_url_change(driver, url_etapa2, timeout=10)

            log('Login BuyTicket realizado com sucesso', 'BT')
            notificar('BuyTicket Login', 'Login realizado com sucesso!')
            break  # sucesso — sai do loop de retentativas

        except Exception as e:
            log(f'Erro no login BuyTicket (tentativa {tentativa+1}/{MAX_RETRIES}): {e}', 'BT')
            screenshot(driver, f'login_erro_{tentativa+1}')
        finally:
            if abriu_nova_aba:
                try:
                    driver.close()
                except Exception:
                    pass
                try:
                    driver.switch_to.window(aba_original)
                except Exception:
                    pass


# URL usada para checar se a sessão continua ativa.
URL_CHECK_SESSAO = os.getenv('BUYTICKET_URL_CONTA', 'https://buyticketbrasil.com/minha-conta')


def buyticket_sessao_ativa(driver):
    """
    Verifica se o login do BuyTicket continua válido.

    Abre uma aba auxiliar, navega a uma URL que exige sessão e conclui
    que a sessão CAIU quando:
      - a URL resultante contém "entrar" ou "login"; OU
      - o campo CPF (novo login set/2026) está presente na página.

    Usa _wait_for em vez de sleep fixo.
    """
    aba_original = driver.current_window_handle
    driver.execute_script("window.open('')")
    abas = driver.window_handles
    driver.switch_to.window(abas[-1])

    caiu = False
    try:
        driver.get(URL_CHECK_SESSAO)
        _wait_for(driver, 'body', timeout=6)

        url_atual = (driver.current_url or '').lower()
        if 'entrar' in url_atual or '/login' in url_atual:
            caiu = True
        else:
            # Novo login (set/2026): campo CPF = input[autocomplete="username"]
            # Legado (fallback): input[placeholder="Seu email"]
            campos_login = driver.find_elements(
                By.CSS_SELECTOR,
                'input[autocomplete="username"], input[placeholder="Seu email"]'
            )
            if campos_login:
                caiu = True
    except Exception as e:
        log(f'Erro ao checar sessao: {e}', 'BT')
        caiu = False
    finally:
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(aba_original)
        except Exception:
            pass

    if caiu:
        log('Checagem de sessao: login CAIU', 'BT')
        return False

    log('Checagem de sessao: login ativo', 'BT')
    return True


def _selecionar_dia_na_pagina(driver, data_show):
    """
    Na nova página do evento (set/2026), clica no botão do dia correto.
    Botão identificado por button[class*="SessionDateRow"] cujo texto contém
    o número do dia E não contém "Camarote".
    Retorna a URL da sessão gerada (/event/.../session/UUID) ou None.
    """
    dia = data_show.split('/')[0]  # "28/10" → "28"
    btns = driver.find_elements(By.CSS_SELECTOR, _SEL_BTN_DATA)
    for btn in btns:
        try:
            txt = (btn.text or '').strip()
            if dia in txt and 'Camarote' not in txt and btn.is_displayed():
                url_antes = driver.current_url
                driver.execute_script('arguments[0].click();', btn)
                log(f'[{data_show}] Botao de data clicado: {txt[:60]}', 'BT')
                # Aguarda URL mudar para /session/
                _wait_for_url_change(driver, url_antes, timeout=10)
                return driver.current_url
        except Exception:
            continue
    log(f'[{data_show}] Botao de data nao encontrado', 'BT')
    return None


def abrir_abas_por_dia(driver, urls, datas):
    """
    Abre uma aba persistente por dia de show e navega cada uma à sua URL de sessão.

    Novo fluxo (set/2026): URLs já apontam diretamente para a sessão do dia
    (/event/slug/session/UUID), sem precisar clicar em botões de data.

    Retorna dict: {data_show: {'handle': ..., 'url': url_sessao}}.
    """
    abas_por_dia = {}
    for idx, (url, data_show) in enumerate(zip(urls, datas)):
        if idx == 0:
            handle = driver.current_window_handle
        else:
            driver.execute_script("window.open('')")
            handle = driver.window_handles[-1]

        driver.switch_to.window(handle)
        try:
            with Cronometro('abrir_aba'):
                driver.get(url)
                _fechar_popup_aviso(driver)
                # Âncora: ComboBox de Tipo de Ingresso confirma que a sessão carregou
                ancora = _wait_for(driver, _SEL_COMBOBOX_TIPO, timeout=15)
                if ancora:
                    log(f'Aba {data_show} carregada: {url}', 'BT')
                else:
                    # Fallback: âncora genérica (pode estar em fila)
                    ancora2 = _wait_for(driver, _SEL_ANCORA_EVENTO, timeout=5)
                    if ancora2:
                        log(f'Aba {data_show} carregada (ancora generica)', 'BT')
                    else:
                        log(f'Aba {data_show}: ancora nao apareceu (continua)', 'BT')
        except Exception as e:
            log(f'Erro ao abrir aba do dia {data_show}: {e}', 'BT')

        abas_por_dia[data_show] = {'handle': handle, 'url': url}

    return abas_por_dia


# ============================================================
# BUYTICKET — SELEÇÃO E COMPRA
# ============================================================

DROPDOWN_TIPO = 0
DROPDOWN_CATEGORIA = 1


def buyticket_ler_preco_botao(driver):
    """
    Lê o preço exibido no botão de compra.
    Novo layout (set/2026): button[class*="PriceActionButton"] com texto "R$ X\nmenor preço\nComprar"
    Legado: a[class*="buyBtn"]
    Retorna int ou None.
    """
    # Novo layout
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, _SEL_BTN_COMPRAR)
        for btn in btns:
            if btn.is_displayed():
                preco = extrair_preco(btn.text)
                if preco:
                    return preco
    except Exception:
        pass
    # Fallback legado
    try:
        buy_btn = driver.find_element(By.CSS_SELECTOR, 'a[class*="buyBtn"]')
        return extrair_preco(buy_btn.text)
    except Exception:
        return None


def _obter_comboboxes(driver):
    """
    Retorna os inputs ComboBox do novo layout (set/2026) em ordem:
    [0] = Tipo de Ingresso, [1] = Categoria.
    Usa seletores por placeholder para evitar confundir com a barra de busca global.
    Fallback: botões button[class*="dropdownHead"] do layout legado.
    """
    # Novo layout: seletores específicos por placeholder
    cb_tipo = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_TIPO)
    cb_cat  = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_CATEGORIA)
    if cb_tipo or cb_cat:
        return cb_tipo + cb_cat  # [0]=Tipo, [1]=Categoria

    # Fallback legado: botões dropdownHead
    return driver.find_elements(By.CSS_SELECTOR, 'button[class*="dropdownHead"]')


def _fechar_dropdown(driver, indice):
    """Fecha o dropdown de índice informado, se estiver aberto."""
    try:
        cbs = _obter_comboboxes(driver)
        if cbs and len(cbs) > indice:
            cb = cbs[indice]
            # Novo layout: pressiona Escape no input para fechar
            if hasattr(cb, 'get_attribute') and cb.get_attribute('role') == 'combobox':
                if cb.get_attribute('aria-expanded') == 'true':
                    cb.send_keys(Keys.ESCAPE)
                    time.sleep(0.1)
                return
            # Legado: botão dropdownHead
            if cb.get_attribute('aria-expanded') == 'true':
                cb.click()
                time.sleep(0.1)
    except Exception:
        pass


def _abrir_dropdown(driver, indice):
    """
    Abre o dropdown de índice informado e aguarda as opções aparecerem.
    Retorna True se abriu.

    Novo layout (set/2026): clica no ComboBox input específico (por placeholder).
    Legado: botão button[class*="dropdownHead"].
    """
    cbs = _obter_comboboxes(driver)
    if not cbs or len(cbs) <= indice:
        log('Dropdowns nao encontrados', 'BT')
        return False

    cb = cbs[indice]
    # Novo layout: input[role="combobox"]
    if cb.get_attribute('role') == 'combobox':
        # Aguarda o ComboBox estar habilitado (Categoria fica disabled até Tipo ser selecionado)
        fim_espera = time.monotonic() + 3.0
        while time.monotonic() < fim_espera:
            if not cb.get_attribute('disabled'):
                break
            time.sleep(0.2)
            # Re-busca o elemento para evitar stale reference
            cbs = _obter_comboboxes(driver)
            if len(cbs) <= indice:
                break
            cb = cbs[indice]

        if cb.get_attribute('aria-expanded') != 'true':
            cb.click()
            # Aguarda opções aparecerem (polling rápido — max 0.5s)
            _wait_for_any(driver, [
                '[class*="ComboBox"][class*="option"]',
                '[class*="ComboBox"][class*="list"]',
                '[role="option"]',
            ], timeout=0.5)
        # Aguarda opções aparecerem — o ComboBox do BuyTicket usa
        # button[class*="ComboBox"][class*="item"] ou div[class*="ComboBox"][class*="list"]
        # como container das opções. Tenta vários seletores.
        for sel_opcoes in [
            f'input[placeholder*="tipo"] ~ * [role="option"]',
            '[class*="ComboBox"][class*="list"]',
            '[class*="ComboBox"][class*="List"]',
            '[class*="ComboBox"][class*="option"]',
            '[class*="ComboBox"][class*="Option"]',
            '[class*="ComboBox"][class*="item"]:not([class*="chevron"])',
        ]:
            els = driver.find_elements(By.CSS_SELECTOR, sel_opcoes)
            if els:
                return True
        # Fallback: se aria-expanded=true, considera aberto mesmo sem lista visível
        return cb.get_attribute('aria-expanded') == 'true'

    # Legado: button[class*="dropdownHead"]
    if cb.get_attribute('aria-expanded') != 'true':
        cb.click()
    resultado = _wait_for(driver, 'ul[role="listbox"]', timeout=3)
    return resultado is not None


def _selecao_confirmada_no_dom(driver, indice, nome_esperado):
    """
    Confirma, via DOM, que a escolha foi efetivamente marcada no dropdown.
    Suporta novo layout ComboBox (input[role="combobox"]) e legado (button).
    """
    alvo = (nome_esperado or '').strip().lower()
    if not alvo:
        return False
    # Novo layout: o value do input reflete o item selecionado
    try:
        cbs = _obter_comboboxes(driver)
        if len(cbs) > indice and cbs[indice].get_attribute('role') == 'combobox':
            valor = (cbs[indice].get_attribute('value') or '').strip().lower()
            if alvo in valor:
                return True
    except Exception:
        pass
    # Legado: texto do botão dropdownHead
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, 'button[class*="dropdownHead"]')
        if len(btns) > indice:
            texto_head = (btns[indice].text or '').strip().lower()
            if alvo in texto_head:
                return True
    except Exception:
        pass
    # Fallback: item com aria-selected="true"
    try:
        selecionados = driver.find_elements(
            By.CSS_SELECTOR, '[aria-selected="true"]'
        )
        for item in selecionados:
            if alvo in (item.text or '').strip().lower():
                return True
    except Exception:
        pass
    return False


def buyticket_selecionar_e_confirmar(driver, indice, nome_alvo, tentativas=MAX_RETRIES):
    """
    Seleciona um item (por nome) no dropdown de `indice` e CONFIRMA no DOM.
    Tenta exatamente MAX_RETRIES vezes.

    Novo layout (set/2026): digita no input[role="combobox"] para filtrar,
    depois clica na opção correspondente na listbox.
    Legado: clica em button[class*="dropdownItem"].
    """
    rotulo = 'Tipo' if indice == DROPDOWN_TIPO else 'Categoria'
    for tentativa in range(tentativas):
        try:
            if not _abrir_dropdown(driver, indice):
                time.sleep(0.1)
                continue

            # ── Novo layout: input combobox ──────────────────────────────
            cbs = _obter_comboboxes(driver)
            if len(cbs) > indice and cbs[indice].get_attribute('role') == 'combobox':
                cb = cbs[indice]
                # Limpa e digita para filtrar
                cb.clear()
                cb.send_keys(nome_alvo)
                time.sleep(0.15)  # debounce do ComboBox Next.js (~100ms)

                # Tenta clicar na opção pelo seletor CSS (re-busca após digitar
                # para evitar stale element da lista anterior)
                clicou = False
                for sel_op in [
                    '[class*="ComboBox"][class*="option"]',
                    '[class*="ComboBox"][class*="item"]:not([class*="chevron"]):not([class*="box"]):not([class*="field"]):not([class*="label"])',
                    '[role="option"]',
                ]:
                    try:
                        els = driver.find_elements(By.CSS_SELECTOR, sel_op)
                        for el in els:
                            try:
                                if el.is_displayed():
                                    texto_opcao = (el.text or '').strip().lower()
                                    # Correspondência EXATA ou inicio do texto (evita "pista" = "camarote + pista")
                                    alvo_lower = nome_alvo.strip().lower()
                                    if texto_opcao == alvo_lower or texto_opcao.startswith(alvo_lower + ' ') or texto_opcao.startswith(alvo_lower + '\n'):
                                        driver.execute_script('arguments[0].click();', el)
                                        clicou = True
                                        log(f'{rotulo}: clicou na opcao "{(el.text or "").strip()[:40]}"', 'BT')
                                        break
                            except Exception:
                                continue
                        if clicou:
                            break
                    except Exception:
                        continue

                if not clicou:
                    # Fallback: tenta via JS buscando opção com texto exato
                    try:
                        resultado_js = driver.execute_script("""
                            var alvo = arguments[0].toLowerCase();
                            var opcoes = document.querySelectorAll('[class*="ComboBox"][class*="option"]');
                            for (var i = 0; i < opcoes.length; i++) {
                                var txt = (opcoes[i].innerText || '').trim().toLowerCase();
                                if (txt === alvo) {
                                    opcoes[i].click();
                                    return 'clicou: ' + txt;
                                }
                            }
                            return null;
                        """, nome_alvo)
                        if resultado_js:
                            clicou = True
                            log(f'{rotulo}: clicou via JS: {resultado_js}', 'BT')
                    except Exception:
                        pass

                if not clicou:
                    # Último recurso: ENTER no campo
                    cbs2 = _obter_comboboxes(driver)
                    if len(cbs2) > indice:
                        cbs2[indice].send_keys(Keys.ENTER)
                    clicou = True
                    log(f'{rotulo}: usou ENTER para selecionar', 'BT')

            else:
                # ── Legado: button[class*="dropdownItem"] ────────────────
                items = driver.find_elements(By.CSS_SELECTOR, 'button[class*="dropdownItem"]')
                clicou = False
                for item in items:
                    nome_el = item.find_elements(By.CSS_SELECTOR, 'span[class*="itemName"]')
                    if nome_el and nome_alvo.strip().lower() in nome_el[0].text.strip().lower():
                        item.click()
                        clicou = True
                        break

                if not clicou:
                    log(f'{rotulo}: item "{nome_alvo}" nao encontrado na lista', 'BT')
                    _fechar_dropdown(driver, indice)
                    continue

            # Polling de confirmação DOM (máx 1s)
            fim = time.monotonic() + 1.0
            while time.monotonic() < fim:
                if _selecao_confirmada_no_dom(driver, indice, nome_alvo):
                    log(f'{rotulo} confirmado no DOM: {nome_alvo} '
                        f'(tentativa {tentativa + 1}/{tentativas})', 'BT')
                    return True
                time.sleep(0.05)

            log(f'{rotulo}: clique nao refletiu no DOM, refazendo '
                f'(tentativa {tentativa + 1}/{tentativas})', 'BT')
            _fechar_dropdown(driver, indice)

        except Exception as e:
            log(f'{rotulo}: erro na selecao (tentativa {tentativa + 1}/{tentativas}): {e}', 'BT')
            _fechar_dropdown(driver, indice)
            time.sleep(0.1)

    log(f'{rotulo}: falha ao confirmar selecao de "{nome_alvo}" apos {tentativas} tentativas', 'BT')
    return False


def _ler_opcoes_combobox_aberto(driver):
    """
    Lê as opções da listbox após um ComboBox ser aberto.
    Retorna lista de dicts {'nome', 'texto_completo'}, sem duplicatas.
    """
    opcoes_vistas = set()
    opcoes = []
    seletores = [
        '[class*="ComboBox"][class*="option"]',
        '[class*="ComboBox"][class*="Option"]',
        '[class*="ComboBox"][class*="item"]:not([class*="chevron"]):not([class*="box"]):not([class*="field"]):not([class*="label"])',
        '[class*="ComboBox"][class*="list"] button',
        '[class*="ComboBox"][class*="List"] button',
        '[role="option"]',
        'li[class*="ComboBox"]',
    ]
    for sel in seletores:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            validos = []
            for e in els:
                try:
                    if e.is_displayed() and (e.text or '').strip():
                        validos.append(e)
                except Exception:
                    continue
            if validos:
                for e in validos:
                    try:
                        txt = e.text.strip()
                        nome = txt.split('\n')[0].strip()
                        # Deduplicar por nome
                        if nome and nome not in opcoes_vistas:
                            opcoes_vistas.add(nome)
                            opcoes.append({'nome': nome, 'texto_completo': txt})
                    except Exception:
                        continue
                if opcoes:
                    log(f'Opcoes encontradas com seletor "{sel}": {[o["nome"] for o in opcoes]}', 'BT')
                    return opcoes
        except Exception:
            continue

    # Fallback JS
    try:
        resultado = driver.execute_script("""
            var opcoes = [];
            var cb = document.querySelector('input[role="combobox"][aria-expanded="true"]');
            if (!cb) return opcoes;
            var container = cb.closest('[class*="field"]') || cb.parentElement;
            for (var i = 0; i < 6; i++) {
                container = container.parentElement;
                if (!container) break;
                var items = container.querySelectorAll('[class*="option"],[class*="Option"]');
                if (items.length > 0) {
                    items.forEach(function(el) {
                        var txt = (el.innerText || '').trim();
                        if (txt) opcoes.push(txt);
                    });
                    break;
                }
            }
            return opcoes;
        """)
        if resultado:
            for t in resultado:
                nome = t.split('\n')[0].strip()
                if nome and nome not in opcoes_vistas:
                    opcoes_vistas.add(nome)
                    opcoes.append({'nome': nome, 'texto_completo': t})
            if opcoes:
                log(f'Opcoes via JS: {[o["nome"] for o in opcoes]}', 'BT')
    except Exception as e:
        log(f'Fallback JS falhou: {e}', 'BT')

    return opcoes


def buyticket_ler_setores_disponiveis(driver):
    """
    Abre o ComboBox de Tipo de Ingresso e lê os setores disponíveis.
    Retorna lista de dicts {'nome', 'preco_min'}.
    Usa clique direto pelo seletor específico para garantir abertura correta.
    """
    setores = []
    try:
        cbs_tipo = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_TIPO)
        if not cbs_tipo:
            log('ComboBox de Tipo nao encontrado', 'BT')
            return setores
        cb = cbs_tipo[0]
        if cb.get_attribute('aria-expanded') != 'true':
            cb.click()
            time.sleep(0.8)
    except Exception as e:
        log(f'Erro ao abrir ComboBox Tipo: {e}', 'BT')
        return setores

    opcoes = _ler_opcoes_combobox_aberto(driver)
    for op in opcoes:
        nome = op['nome']
        preco_min = extrair_preco(op['texto_completo'])
        setores.append({'nome': nome, 'preco_min': preco_min})
        log(f'Tipo disponivel: {nome} = R${preco_min}', 'BT')

    # Fechar
    try:
        cbs_tipo = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_TIPO)
        if cbs_tipo and cbs_tipo[0].get_attribute('aria-expanded') == 'true':
            cbs_tipo[0].send_keys('\x1b')
            time.sleep(0.2)
    except Exception:
        pass

    return setores


def buyticket_ler_categorias_disponiveis(driver):
    """
    Abre o ComboBox de Categoria e lê as categorias disponíveis.
    Retorna lista de dicts {'nome', 'preco'}.
    Usa clique direto pelo seletor específico para garantir abertura correta.
    """
    categorias = []
    try:
        # Clique direto no ComboBox de Categoria pelo placeholder específico
        cbs_cat = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_CATEGORIA)
        if not cbs_cat:
            log('ComboBox de Categoria nao encontrado', 'BT')
            return categorias
        cb = cbs_cat[0]
        # Aguarda habilitar (pode estar disabled antes do Tipo ser selecionado)
        fim = time.monotonic() + 3.0
        while time.monotonic() < fim and cb.get_attribute('disabled'):
            time.sleep(0.2)
            cbs_cat = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_CATEGORIA)
            if cbs_cat:
                cb = cbs_cat[0]
        if cb.get_attribute('disabled'):
            log('ComboBox Categoria ainda disabled apos espera', 'BT')
            return categorias
        # Clicar para abrir
        if cb.get_attribute('aria-expanded') != 'true':
            cb.click()
            time.sleep(0.8)
    except Exception as e:
        log(f'Erro ao abrir ComboBox Categoria: {e}', 'BT')
        return categorias

    opcoes = _ler_opcoes_combobox_aberto(driver)
    for op in opcoes:
        nome = op['nome']
        preco = extrair_preco(op['texto_completo'])
        categorias.append({'nome': nome, 'preco': preco})
        log(f'Categoria disponivel: {nome} = R${preco}', 'BT')

    # Fechar
    try:
        cbs_cat = driver.find_elements(By.CSS_SELECTOR, _SEL_COMBOBOX_CATEGORIA)
        if cbs_cat and cbs_cat[0].get_attribute('aria-expanded') == 'true':
            cbs_cat[0].send_keys('\x1b')  # ESC
            time.sleep(0.2)
    except Exception:
        pass

    return categorias


def buyticket_fluxo_compra(driver, data_show, url=None):
    """
    Fluxo de compra resiliente.

    V1: valida preço do botão contra MAIOR_LIMITE.
    Mapeia setores, filtra por limite, ordena por prioridade.
    Para cada setor válido tenta até MAX_CICLOS ciclos.
    """
    log(f'Iniciando fluxo de compra para {data_show}', 'BT')
    screenshot(driver, f'fluxo_inicio_{data_show.replace("/", "")}')

    with Cronometro('validacao_botao'):
        preco_botao = buyticket_ler_preco_botao(driver)
        ok, motivo = validar_botao_inicial(preco_botao, MAIOR_LIMITE)

    if not ok:
        if motivo == 'sem_preco':
            log('V1: erro de parametro inicial - sem preco no botao', 'BT')
        else:
            log(f'V1: preco fora dos parametros - R${preco_botao} acima do maior '
                f'limite R${MAIOR_LIMITE} (sem notificacao)', 'BT')
        return False
    log(f'V1 OK: R${preco_botao} dentro da faixa (maior limite R${MAIOR_LIMITE})', 'BT')

    setores_disponiveis = buyticket_ler_setores_disponiveis(driver)
    setores_validos = filtrar_setores_validos(setores_disponiveis, LIMITES_PRECO)
    if not setores_validos:
        log('Nenhum setor valido dentro dos limites', 'BT')
        notificar(
            'Fora dos limites por setor',
            f'{data_show}: R${preco_botao} passou no teto geral, mas nenhum setor'
            f' encaixou nos seus limites. Nada comprado.',
            'high',
        )
        return False
    nomes_validos = ', '.join(s['nome'] for s in setores_validos)
    log('Combinacoes por prioridade: ' + nomes_validos, 'BT')
    notificar(
        'Ingresso na faixa!',
        f'{data_show}: setor(es) dentro do limite: {nomes_validos}. Comprando...',
        'high',
    )

    for setor in setores_validos:
        if buyticket_processar_combinacao(driver, data_show, url, setor):
            return True

    log('Nenhuma combinacao resultou em compra', 'BT')
    notificar(
        'Compra nao concluida',
        f'{data_show}: ingresso encontrado e setores validados, mas a compra nao'
        f' foi concluida (divergencia de valor ou ciclos esgotados). Monitorando...',
    )
    return False


def buyticket_processar_combinacao(driver, data_show, url, setor):
    """
    Processa uma combinação (setor) com até MAX_CICLOS ciclos completos.
    Retorna True somente quando a compra foi disparada com sucesso.
    """
    nome_setor = setor['nome']
    setor_key = setor['key']

    for ciclo in range(MAX_CICLOS):
        log(f'[{nome_setor}] ciclo {ciclo + 1}/{MAX_CICLOS}', 'BT')

        with Cronometro('selecao_tipo'):
            ok_tipo = buyticket_selecionar_e_confirmar(driver, DROPDOWN_TIPO, nome_setor)

        if not ok_tipo:
            log(f'[{nome_setor}] falha ao confirmar Tipo no ciclo {ciclo + 1}', 'BT')
            if buyticket_recarregar(driver, url, ciclo):
                continue
            break

        # Aguarda o ComboBox de Categoria habilitar após seleção do Tipo
        # Polling: aguarda o ComboBox Categoria ficar enabled (sem sleep fixo)
        _wait_for(driver, f'input[role="combobox"][placeholder*="categoria"]:not([disabled])', timeout=3)
        categorias_disponiveis = buyticket_ler_categorias_disponiveis(driver)
        categorias_validas = filtrar_categorias_validas(
            categorias_disponiveis, setor_key, LIMITES_PRECO
        )
        if not categorias_validas:
            log(f'[{nome_setor}] nenhuma categoria valida dentro do limite', 'BT')
            return False

        # Ordena: preço conhecido menor primeiro, sem preço por último
        cat = min(categorias_validas,
                  key=lambda c: (c.get('preco') is None or c.get('preco', 0) == 0,
                                 c.get('preco') or 999999))

        with Cronometro('selecao_categoria'):
            ok_cat = buyticket_selecionar_e_confirmar(
                driver, DROPDOWN_CATEGORIA, cat['nome']
            )

        if not ok_cat:
            log(f'[{nome_setor}/{cat["nome"]}] falha ao confirmar Categoria '
                f'no ciclo {ciclo + 1}', 'BT')
            if buyticket_recarregar(driver, url, ciclo):
                continue
            break

        # Reler o preço do botão APÓS selecionar a categoria.
        # No novo layout o preço muda quando Tipo + Categoria são selecionados.
        # O preço no ComboBox pode não refletir o preço real do botão.
        time.sleep(0.5)
        preco_botao = buyticket_ler_preco_botao(driver)

        # Se a categoria não tinha preço no ComboBox (ou estava acima do limite),
        # usar o preço do botão como referência real.
        preco_cat_real = preco_botao  # sempre usa o preço real do botão após seleção

        # Validar que o preço do botão está dentro do limite da categoria.
        if preco_botao:
            limite_cat = cat.get('limite', 0)
            if preco_botao > limite_cat:
                log(f'[{nome_setor}/{cat["nome"]}] preco real do botao R${preco_botao} '
                    f'acima do limite R${limite_cat} -> pulando', 'BT')
                _fechar_dropdown(driver, DROPDOWN_CATEGORIA)
                if buyticket_recarregar(driver, url, ciclo):
                    continue
                break

        ok, motivo = validar_valor_final(preco_botao, preco_cat_real)
        if ok:
            log(f'[{nome_setor}/{cat["nome"]}] valor OK: botao R${preco_botao} '
                f'<= limite R${cat["limite"]} -> COMPRANDO!', 'BT')

            # Aguarda o lock — garante que só 1 thread por vez compra
            if not _COMPRA_LOCK.acquire(blocking=True, timeout=600):
                log(f'[{nome_setor}] Timeout aguardando lock de compra (10min)', 'BT')
                break
            try:
                log(f'[{data_show}] Lock de compra adquirido. Iniciando checkout...', 'BT')
                notificar(
                    'COMPRANDO AGORA!',
                    f'{nome_setor} / {cat["nome"]}\nR${preco_botao} (limite R${cat["limite"]})\n'
                    f'Dia: {data_show}',
                    'urgent',
                )
                notificar_pc(
                    '🛒 Comprando agora!',
                    f'{nome_setor} / {cat["nome"]}  R${preco_botao}\nDia: {data_show}',
                    'high',
                )
                resultado = buyticket_efetuar_compra(
                    driver, data_show, nome_setor, cat['nome'], preco_botao
                )
            finally:
                _COMPRA_LOCK.release()
                log(f'[{data_show}] Lock de compra liberado.', 'BT')

            if resultado is True:
                return True

            if resultado == 'em_andamento':
                log(f'[{nome_setor}] PIX pendente — aguardando 3 minutos antes de nova tentativa...', 'BT')
                time.sleep(180)  # 3 min: tempo suficiente para o PIX expirar
                _refresh_mantendo_dia(driver, url)
                ciclo = 0
                continue

            if resultado == 'expirado':
                log(f'[{nome_setor}] PIX expirou. Verificando se ingresso ainda esta disponivel...', 'BT')
                notificar('Retentando', f'{data_show}: PIX expirou, verificando novo ingresso...')
                _refresh_mantendo_dia(driver, url)
                preco_atual = buyticket_ler_preco_botao(driver)
                if preco_atual and preco_atual <= MAIOR_LIMITE:
                    log(f'[{nome_setor}] Ingresso ainda disponivel (R${preco_atual}). Repetindo fluxo...', 'BT')
                    ciclo = 0
                    continue
                else:
                    log(f'[{nome_setor}] Ingresso nao disponivel ou acima do limite. Encerrando.', 'BT')
                    return False

            return False

        log(f'[{nome_setor}/{cat["nome"]}] divergencia de valores: botao R${preco_botao} '
            f'!= esperado R${preco_cat_real} (motivo={motivo}) -> recarregando e refazendo '
            f'(sem notificacao)', 'BT')
        if not buyticket_recarregar(driver, url, ciclo):
            break

    log(f'[{nome_setor}] ciclos esgotados ({MAX_CICLOS}) sem sucesso, proxima combinacao', 'BT')
    return False


def buyticket_recarregar(driver, url, ciclo):
    """Recarrega a página para reiniciar a seleção no próximo ciclo."""
    if ciclo >= MAX_CICLOS - 1:
        return False
    _refresh_mantendo_dia(driver, url)
    return True


def _refresh_mantendo_dia(driver, url):
    """
    Recarrega a página atual preservando a URL de sessão do dia.
    Com URLs de sessão fixas, se o refresh cair fora da sessão navega
    de volta direto para a URL correta.
    """
    with Cronometro('refresh'):
        try:
            driver.refresh()
            time.sleep(1)
            _fechar_popup_aviso(driver)
            # Se caiu fora da URL de sessão, navega de volta
            cur = driver.current_url or ''
            if url and url not in cur and '/session/' in url:
                log('Refresh saiu da sessao, voltando para URL correta...', 'BT')
                driver.get(url)
                time.sleep(2)
                _fechar_popup_aviso(driver)
            _wait_for(driver, _SEL_ANCORA_EVENTO, timeout=10)
        except Exception as e:
            log(f'Erro ao recarregar pagina: {e}', 'BT')
            if url:
                try:
                    driver.get(url)
                    time.sleep(2)
                    _fechar_popup_aviso(driver)
                    _wait_for(driver, _SEL_ANCORA_EVENTO, timeout=12)
                except Exception as e2:
                    log(f'Erro no fallback de recarregar: {e2}', 'BT')


def buyticket_refresh_dia(driver, aba):
    """Troca para a aba do dia e recarrega mantendo a página do dia."""
    try:
        driver.switch_to.window(aba['handle'])
    except Exception as e:
        log(f'Erro ao trocar para aba do dia: {e}', 'BT')
        return
    _refresh_mantendo_dia(driver, aba.get('url'))


# ============================================================
# BUYTICKET — CHECKOUT
# ============================================================

# Seletores CSS confirmados da fila CrowdHandler (capturas/10-fila.html)
_FILA_SEL_ATIVO   = 'section#ch-active'
_FILA_SEL_BLOQ    = 'section#ch-blocked'
_FILA_SEL_POSICAO = 'strong#ch-count'
_FILA_SEL_MINUTOS = '#ch-estimated-minutes .ch-number'
_FILA_DOMINIO     = 'wait.crowdhandler.com'

_FILA_TEXTOS_FB = (
    'todo mundo quer nossos ingressos',
    'posição na fila',
    'posicao na fila',
    'crowdhandler',
    'ch-active',
)

_FILA_POLL_S      = 0.10
_FILA_LOG_N_POLLS = 30


def _em_fila_de_espera(driver):
    """Detecta se o site exibiu a tela de fila CrowdHandler."""
    try:
        if _FILA_DOMINIO in (driver.current_url or '').lower():
            return True
        els = driver.find_elements(By.CSS_SELECTOR, _FILA_SEL_ATIVO)
        if els and els[0].is_displayed():
            return True
        src = driver.page_source.lower()
        return any(t in src for t in _FILA_TEXTOS_FB)
    except Exception:
        return False


def _ler_posicao_fila_bot(driver):
    """Lê posição e tempo estimado da fila CrowdHandler."""
    pos, mins = '?', '?'
    try:
        els = driver.find_elements(By.CSS_SELECTOR, _FILA_SEL_POSICAO)
        if els:
            pos = (els[0].text or '?').strip()
    except Exception:
        pass
    try:
        els = driver.find_elements(By.CSS_SELECTOR, _FILA_SEL_MINUTOS)
        if els:
            mins = (els[0].text or '?').strip()
    except Exception:
        pass
    return pos, mins


def _aguardar_sair_da_fila(driver, timeout_min=20):
    """
    Aguarda a liberação da fila CrowdHandler com polling de alta performance.
    Polling a 100ms; retorna True imediatamente quando liberado.
    """
    if not _em_fila_de_espera(driver):
        return True

    pos_ini, mins_ini = _ler_posicao_fila_bot(driver)
    log(f'Fila detectada! Posicao: {pos_ini} | ~{mins_ini} min de espera. '
        f'Polling a {int(_FILA_POLL_S * 1000)}ms...', 'BT')
    notificar(
        'Fila de Espera',
        f'Bot na fila do BuyTicket: posicao {pos_ini}, ~{mins_ini} min. Aguardando...',
    )

    t_inicio = time.monotonic()
    t_limite = t_inicio + timeout_min * 60
    n_poll = 0

    while time.monotonic() < t_limite:
        time.sleep(_FILA_POLL_S)
        n_poll += 1

        try:
            if not _em_fila_de_espera(driver):
                duracao = time.monotonic() - t_inicio
                log(f'FILA LIBERADA em {duracao:.1f}s '
                    f'(posicao inicial: {pos_ini}). Continuando checkout...', 'BT')
                notificar('Fila Liberada!',
                          f'Saiu da fila em {duracao:.0f}s. Executando compra...',
                          'high')
                return True

            try:
                bloq = driver.find_elements(By.CSS_SELECTOR, _FILA_SEL_BLOQ)
                if bloq and bloq[0].is_displayed():
                    log('Acesso BLOQUEADO pelo CrowdHandler. Abortando.', 'BT')
                    notificar('Acesso Bloqueado',
                              'CrowdHandler bloqueou o acesso por atividade suspeita.',
                              'urgent')
                    return False
            except Exception:
                pass

            if n_poll % _FILA_LOG_N_POLLS == 0:
                pos, mins = _ler_posicao_fila_bot(driver)
                restante = int((t_limite - time.monotonic()) / 60)
                log(f'Na fila: posicao {pos} | ~{mins} min | timeout em {restante} min', 'BT')

        except Exception as e:
            log(f'Erro no polling de fila: {e}', 'BT')

    duracao = time.monotonic() - t_inicio
    log(f'Timeout aguardando sair da fila ({timeout_min} min, {duracao:.0f}s decorridos).', 'BT')
    notificar('Timeout Fila', f'Nao saiu da fila em {timeout_min} min. Abortando tentativa.')
    return False


def _aguardar_overlay_sumir(driver, timeout=8):
    """
    Aguarda o overlay/backdrop de modal (`div.greyout`) desaparecer.
    Usa _wait_until_gone em vez de polling manual com sleep (Task 4).
    """
    return _wait_until_gone(driver, 'div[class*="greyout"]', timeout=timeout)


def _pix_esta_selecionado(driver):
    """
    Verifica se o método PIX está efetivamente selecionado.
    Novo layout (set/2026): button[class*="PaymentMethodRow"] com aria-selected ou aria-pressed,
    ou que contenha classe "selected"/"active" após o clique.
    """
    try:
        # Novo layout: button de método de pagamento
        for btn in driver.find_elements(By.CSS_SELECTOR, 'button[class*="PaymentMethodRow"]'):
            texto = (btn.text or '').upper()
            if 'PIX' in texto:
                cls = (btn.get_attribute('class') or '').lower()
                aria = (btn.get_attribute('aria-selected') or
                        btn.get_attribute('aria-pressed') or
                        btn.get_attribute('aria-checked') or '').lower()
                if aria == 'true' or 'selected' in cls or 'active' in cls or 'checked' in cls:
                    return True
                # Verificar via JS se o botão está "marcado"
                selecionado = driver.execute_script(
                    "return arguments[0].getAttribute('data-state') === 'active' || "
                    "arguments[0].classList.contains('selected') || "
                    "window.getComputedStyle(arguments[0]).getPropertyValue('border-color').includes('rgb(99, 91, 255)')",
                    btn
                )
                if selecionado:
                    return True
    except Exception:
        pass

    # Legado: radio button
    try:
        for r in driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"][value="PIX"]'):
            if r.is_selected() or r.get_attribute('checked'):
                return True
    except Exception:
        pass
    return False


def _selecionar_pix(driver, tentativas=MAX_RETRIES):
    """
    Seleciona o método de pagamento PIX.

    Novo layout (set/2026): button[class*="PaymentMethodRow"] com texto "PIX".
    Legado: input[type="radio"][value="PIX"] ou label:has-text("PIX").

    Tenta MAX_RETRIES vezes.
    """
    for tentativa in range(tentativas):
        _aguardar_overlay_sumir(driver)

        # Novo layout: botão PaymentMethodRow com texto PIX
        alvos = [
            btn for btn in driver.find_elements(By.CSS_SELECTOR, 'button[class*="PaymentMethodRow"]')
            if 'PIX' in (btn.text or '').upper() and btn.is_displayed()
        ]

        # Legado: radio nativo
        if not alvos:
            alvos = driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"][value="PIX"]')

        # Legado: labels com texto PIX
        if not alvos:
            alvos = [
                el for el in driver.find_elements(By.CSS_SELECTOR, 'label')
                if 'pix' in (el.text or '').lower() and el.is_displayed()
            ]

        for alvo in alvos:
            try:
                if not alvo.is_displayed():
                    continue
            except Exception:
                continue

            _clique_resiliente(driver, alvo)

            # Polling de confirmação (máx 1s)
            for _ in range(5):
                time.sleep(0.2)
                if _pix_esta_selecionado(driver):
                    log(f'PIX selecionado (tentativa {tentativa + 1}/{tentativas})', 'BT')
                    return True

        log(f'PIX nao confirmou selecao, refazendo (tentativa {tentativa + 1}/{tentativas})', 'BT')

    log('Nao foi possivel confirmar selecao de PIX', 'BT')
    return False


def _cartao_credito_selecionado(driver):
    """Verifica se cartão de crédito está efetivamente selecionado."""
    try:
        # Bubble.io — radio nativo com value exato
        for r in driver.find_elements(
            By.CSS_SELECTOR,
            'input[type="radio"][value="Cartão de crédito"], '
            'input[type="radio"][value="Cartao de credito"]',
        ):
            if r.is_selected() or r.get_attribute('checked'):
                return True
    except Exception:
        pass
    try:
        # Novo layout Next.js — PaymentMethodRow
        for btn in driver.find_elements(By.CSS_SELECTOR, 'button[class*="PaymentMethodRow"]'):
            txt = (btn.text or '').lower()
            if any(k in txt for k in ['cartão', 'cartao', 'crédito', 'credito']):
                cls  = (btn.get_attribute('class') or '').lower()
                aria = (btn.get_attribute('aria-selected') or
                        btn.get_attribute('aria-pressed') or '').lower()
                if aria == 'true' or 'selected' in cls or 'active' in cls:
                    return True
    except Exception:
        pass
    return False


def _selecionar_cartao_credito(driver, tentativas=MAX_RETRIES):
    """
    Seleciona o método de pagamento Cartão de Crédito.

    Suporta:
      - Bubble.io: input[type="radio"][value="Cartão de crédito"] (confirmado nas capturas)
      - Legado label com texto "Cartão de crédito"
      - Novo layout Next.js: button[class*="PaymentMethodRow"] com texto cartão
    """
    for tentativa in range(tentativas):
        _aguardar_overlay_sumir(driver)

        # 1. Radio nativo Bubble — value pode vir com ou sem acento (encoding do site)
        alvos = driver.find_elements(
            By.CSS_SELECTOR,
            'input[type="radio"][value="Cartão de crédito"], '
            'input[type="radio"][value="Cartao de credito"]',
        )

        # 2. Label associado ao radio (clicar no label também marca o radio)
        if not alvos:
            alvos = [
                el for el in driver.find_elements(By.CSS_SELECTOR, 'label')
                if any(k in (el.text or '').lower()
                       for k in ['cartão de crédito', 'cartao de credito', 'crédito'])
                and el.is_displayed()
            ]

        # 3. Novo layout Next.js — PaymentMethodRow
        if not alvos:
            alvos = [
                btn for btn in driver.find_elements(
                    By.CSS_SELECTOR, 'button[class*="PaymentMethodRow"]')
                if any(k in (btn.text or '').lower()
                       for k in ['cartão', 'cartao', 'crédito', 'credito'])
                and btn.is_displayed()
            ]

        for alvo in alvos:
            try:
                if not alvo.is_displayed():
                    continue
                _clique_resiliente(driver, alvo)
                for _ in range(5):
                    time.sleep(0.2)
                    if _cartao_credito_selecionado(driver):
                        log(f'Cartão de crédito selecionado (tent. {tentativa+1}/{tentativas})', 'BT')
                        return True
            except Exception:
                continue

        log(f'Cartão não confirmou seleção, tent. {tentativa+1}/{tentativas}', 'BT')

    log('Não foi possível selecionar cartão de crédito', 'BT')
    return False


def _selecionar_parcelas(driver, n=3, tentativas=MAX_RETRIES):
    """
    Seleciona o número de parcelas após cartão de crédito estar selecionado.

    Suporta múltiplos layouts:
      1. <select> nativo Bubble com options "1x", "2x", "3x" ...
      2. input[type="radio"] com value começando em "3"
      3. label / botão / div[role="option"] com texto começando em "3x"
      4. Combobox customizado Bubble com placeholder "parcela"
    """
    alvo_texto = f'{n}x'

    for tentativa in range(tentativas):
        # Pequena pausa para o DOM atualizar após selecionar cartão
        time.sleep(0.5)

        # 1. <select> nativo
        try:
            from selenium.webdriver.support.ui import Select as _Sel
            for sel in driver.find_elements(By.CSS_SELECTOR, 'select'):
                opts = sel.find_elements(By.TAG_NAME, 'option')
                for opt in opts:
                    if opt.text.strip().startswith(alvo_texto):
                        _Sel(sel).select_by_visible_text(opt.text.strip())
                        log(f'Parcelas: "{opt.text.strip()}" via <select>', 'BT')
                        return True
        except Exception:
            pass

        # 2. Radio com value iniciando em str(n)
        try:
            for r in driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"]'):
                val = (r.get_attribute('value') or '').strip()
                if val.startswith(str(n)) and r.is_displayed():
                    _clique_resiliente(driver, r)
                    time.sleep(0.2)
                    log(f'Parcelas: radio "{val}" clicado', 'BT')
                    return True
        except Exception:
            pass

        # 3. Qualquer label/botão/div com texto "3x"
        try:
            for el in driver.find_elements(
                By.CSS_SELECTOR, 'label, button, div[role="option"], li[role="option"]'
            ):
                txt = (el.text or '').strip()
                if txt.startswith(alvo_texto) and el.is_displayed():
                    _clique_resiliente(driver, el)
                    time.sleep(0.2)
                    log(f'Parcelas: clicou em "{txt}"', 'BT')
                    return True
        except Exception:
            pass

        # 4. Combobox Bubble com placeholder "parcela"
        try:
            for combo in driver.find_elements(By.CSS_SELECTOR, 'input[role="combobox"]'):
                ph = (combo.get_attribute('placeholder') or '').lower()
                if any(k in ph for k in ['parcela', 'parcel', 'installment']):
                    combo.click()
                    time.sleep(0.4)
                    for opt_el in driver.find_elements(
                        By.CSS_SELECTOR,
                        'div[role="option"], li[role="option"], .bubble-element.Option',
                    ):
                        if (opt_el.text or '').strip().startswith(alvo_texto):
                            _clique_resiliente(driver, opt_el)
                            log(f'Parcelas: "{opt_el.text.strip()}" via combobox', 'BT')
                            return True
        except Exception:
            pass

        log(f'Parcelas {n}x: tentativa {tentativa+1}/{tentativas} sem sucesso', 'BT')

    log(f'Não foi possível selecionar {n}x parcelas', 'BT')
    return False


def _continuar_esta_habilitado(btn):
    """Heurística: botão habilitado (sem disabled e sem opacity baixa)."""
    try:
        if btn.get_attribute('disabled'):
            return False
        estilo = (btn.get_attribute('style') or '').replace(' ', '')
        if 'opacity:0.7' in estilo or 'cursor:default' in estilo:
            return False
    except Exception:
        pass
    return True


def _clicar_continuar(driver, timeout=8):
    """
    Clica no botão "Continuar" QUANDO ele estiver habilitado.

    Usa _btn_por_texto em vez de XPath (Task 5). Tenta até MAX_RETRIES
    vezes e aguarda habilitação via polling com _wait_for (Task 4).
    """
    with Cronometro('click_continuar'):
        _aguardar_overlay_sumir(driver)
        fim = time.monotonic() + timeout
        while time.monotonic() < fim:
            btns = _btn_por_texto(driver, 'Continuar')
            for btn in btns:
                if _continuar_esta_habilitado(btn):
                    url_antes = driver.current_url
                    _clique_resiliente(driver, btn)
                    log('Clicou "Continuar"', 'BT')
                    # Aguarda mudança de estado pós-clique (Task 4)
                    _wait_for_url_change(driver, url_antes, timeout=4)
                    return True
            time.sleep(0.2)
        log('Botao "Continuar" nao habilitou a tempo', 'BT')
        return False


def _ainda_na_tela_de_selecao(driver):
    """Detecta se a página ainda está na tela de seleção de ingresso."""
    # Novo layout: PriceActionButton + URL da sessão
    try:
        url = (driver.current_url or '').lower()
        if '/checkout' in url:
            return False  # já avançou para o checkout
        btns = driver.find_elements(By.CSS_SELECTOR, _SEL_BTN_COMPRAR)
        for b in btns:
            if b.is_displayed():
                return True
    except Exception:
        pass
    # Fallback legado
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, 'a[class*="buyBtn"]')
        for b in btns:
            if b.is_displayed() and 'comprar' in (b.text or '').lower():
                return True
    except Exception:
        pass
    return False


def _fechar_modal_seguranca(driver):
    """
    Fecha o modal anti-golpe "Compre com segurança só pela BuyTicket".
    Usa _btn_por_texto em vez de XPath (Task 5).
    Aguarda overlay sumir após fechar (Task 4).
    """
    # Botão "Combinado" (CSS + filtragem Python — Task 5)
    btns = _btn_por_texto(driver, 'Combinado')
    for b in btns:
        _clique_resiliente(driver, b)
        log('Fechou modal de seguranca (Combinado)', 'BT')
        _aguardar_overlay_sumir(driver, timeout=4)
        return True

    # Fallback: botão de fechar por aria-label
    try:
        fechar = [
            b for b in driver.find_elements(By.CSS_SELECTOR, 'button')
            if (b.get_attribute('aria-label') or '').lower() in ('fechar', 'close')
            or (b.text or '').strip() in ('×', 'X')
        ]
        for b in fechar:
            if b.is_displayed():
                _clique_resiliente(driver, b)
                log('Fechou modal de seguranca (x)', 'BT')
                _aguardar_overlay_sumir(driver, timeout=4)
                return True
    except Exception:
        pass
    return False


def _confirmar_avanco_checkout(driver, tentativas=MAX_RETRIES):
    """
    Garante que o fluxo saiu da tela de seleção após clicar em COMPRAR.
    Fecha modal anti-golpe e reclica se necessário (Task 4/5).
    """
    for tentativa in range(tentativas):
        _fechar_modal_seguranca(driver)
        # Aguarda sair da tela de seleção (novo: URL muda para /checkout)
        _wait_for_url_change(driver, driver.current_url, timeout=4)
        if not _ainda_na_tela_de_selecao(driver):
            return True
        log(f'Checkout nao avancou (ainda na selecao), reclicando COMPRAR '
            f'(tentativa {tentativa + 1}/{tentativas})', 'BT')
        try:
            # Novo layout: PriceActionButton
            buy_btn = None
            for b in driver.find_elements(By.CSS_SELECTOR, _SEL_BTN_COMPRAR):
                if b.is_displayed():
                    buy_btn = b
                    break
            if not buy_btn:
                buy_btn = driver.find_element(By.CSS_SELECTOR, 'a[class*="buyBtn"]')
            _clique_resiliente(driver, buy_btn)
            # Aguarda mudança de estado pós-clique (Task 4)
            _wait_for_any(driver, [
                'div[class*="greyout"]',
                'section#ch-active',
                'button[class*="continuar"], button',
            ], timeout=6)
        except Exception as e:
            log(f'Erro ao reclicar COMPRAR: {e}', 'BT')

    _fechar_modal_seguranca(driver)
    return not _ainda_na_tela_de_selecao(driver)


def _esperar_elemento_ou_fila(driver, selector, timeout=6):
    """
    Aguarda até `timeout` segundos que um seletor CSS apareça OU que a fila
    CrowdHandler seja detectada. Retorna (elemento_encontrado, em_fila).
    """
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        if _em_fila_de_espera(driver):
            return False, True
        try:
            els = driver.find_elements(By.CSS_SELECTOR, selector)
            visiveis = [e for e in els if e.is_displayed()]
            if visiveis:
                return True, False
        except Exception:
            pass
        time.sleep(0.12)
    return False, False


def buyticket_efetuar_compra(driver, data_show, setor, categoria, preco):
    """Clica no botão COMPRAR e segue para o checkout."""
    if DRY_RUN:
        log(f'[DRY_RUN] Compra SIMULADA (nao clicou comprar): {setor}/{categoria} '
            f'R${preco} - {data_show}', 'BT')
        notificar('DRY_RUN - Compra Simulada',
                  f'{data_show} {setor}/{categoria} R${preco} (nenhuma compra real efetuada)')
        return False

    with Cronometro('click_comprar'):
        try:
            # Novo layout Next.js (set/2026): button[class*="PriceActionButton"]
            # Legado Bubble: a[class*="buyBtn"]
            buy_btn = None
            btns_pab = driver.find_elements(By.CSS_SELECTOR, _SEL_BTN_COMPRAR)
            for b in btns_pab:
                if b.is_displayed() and 'Comprar' in (b.text or ''):
                    buy_btn = b
                    break
            if not buy_btn:
                # Fallback legado
                try:
                    buy_btn = driver.find_element(By.CSS_SELECTOR, 'a[class*="buyBtn"]')
                except Exception:
                    pass
            if not buy_btn:
                log('Botao COMPRAR nao encontrado', 'BT')
                return False
            _clique_resiliente(driver, buy_btn)
            log('Botao COMPRAR clicado!', 'BT')
        except Exception as e:
            log(f'Erro ao clicar comprar: {e}', 'BT')
            return False

        # Aguarda algum estado pós-clique em vez de sleep fixo (Task 4)
        _wait_for_any(driver, [
            'div[class*="greyout"]',  # modal de segurança
            'section#ch-active',      # fila CrowdHandler
            'button',                 # próxima tela
        ], timeout=8)

    url_pos_comprar = (driver.current_url or '').lower()

    # Redirecionamento para login: autentica e sinaliza refazer
    if 'entrar' in url_pos_comprar:
        log('Redirecionado para login...', 'BT')
        buyticket_login(driver)
        return False

    # Verifica se o COMPRAR realmente navegou para o checkout.
    # Se o URL ainda for a página do evento, houve bloqueio (compra em andamento)
    # ou falha de navegação — não chama buyticket_checkout.
    em_checkout = '/checkout' in url_pos_comprar or '/sacola' in url_pos_comprar
    if not em_checkout:
        src_check = driver.page_source.lower()
        if 'compra em andamento' in src_check or 'finalize ou aguarde' in src_check:
            log('PIX pendente detectado ("compra em andamento") na pág. evento. Aguardando 3min...', 'BT')
            notificar('Compra em andamento',
                      f'{data_show}: PIX anterior pendente — aguardando 3 min para expirar.')
            screenshot(driver, f'em_andamento_{data_show.replace("/", "")}')
            return 'em_andamento'
        else:
            log(f'COMPRAR nao navegou para checkout (URL: {url_pos_comprar[:60]}). '
                f'Verificando...', 'BT')
            # Tenta aguardar navegação por mais 3s
            _wait_for_url_change(driver, driver.current_url, timeout=3)
            url_pos_comprar = (driver.current_url or '').lower()
            em_checkout = '/checkout' in url_pos_comprar or '/sacola' in url_pos_comprar
            if not em_checkout:
                log('Checkout nao carregou apos espera. Abortando.', 'BT')
                return False

    return buyticket_checkout(driver, data_show, setor, categoria, preco)


def buyticket_checkout(driver, data_show, setor, categoria, preco):
    """
    Completa o fluxo de checkout após clicar em Comprar.

    Novo fluxo (set/2026):
      1. Modal de reserva "Continuar" — abre a tela de checkout.
      2. Fila CrowdHandler — detecta e aguarda se presente.
      3. Selecionar Cartão de Crédito.
      3b. Selecionar 3x parcelas (quando disponível).
      4. Preencher e-mail do recebedor.
      5. Clicar "Pagar" — abre sheet de dados de cobrança.
      6. Preencher dados de cobrança.
      7. Clicar "Comprar agora".
      8. Aguardar confirmação do gateway (cartão).
         → Se não conseguir automaticamente: página fica ABERTA para conclusão manual.
    """
    try:
        # Única screenshot de entrada (removido duplicado)
        screenshot(driver, f'compra_{data_show.replace("/", "")}')

        # ── 1. Modal de reserva "Continuar" ─────────────────────────────────
        # Sem sleep fixo — aguarda o modal aparecer diretamente
        if not _aguardar_sair_da_fila(driver):
            log('Abortando checkout: fila.', 'BT')
            return False

        with Cronometro('modal_checkout'):
            log('Procurando modal de 10 minutos...', 'BT')
            clicou_continuar = False
            fim = time.monotonic() + 8
            while time.monotonic() < fim:
                if _em_fila_de_espera(driver):
                    if not _aguardar_sair_da_fila(driver):
                        return False
                btns = _btn_por_texto(driver, 'Continuar')
                for b in btns:
                    driver.execute_script('arguments[0].click();', b)
                    log('Clicou "Continuar" (modal checkout) via JS', 'BT')
                    clicou_continuar = True
                    break
                if clicou_continuar:
                    # Aguarda URL mudar ao invés de sleep fixo
                    _wait_for_url_change(driver, driver.current_url, timeout=3)
                    break
                time.sleep(0.1)

        log(f'URL checkout apos modal: {driver.current_url}', 'BT')

        # ── 2. Fila após modal ───────────────────────────────────────────────
        if not _aguardar_sair_da_fila(driver):
            return False

        # ── 3. Selecionar PIX ────────────────────────────────────────────────
        with Cronometro('selecionar_pix'):
            if not _selecionar_pix(driver):
                log('Nao selecionou PIX.', 'BT')
                notificar('PIX nao selecionado',
                          f'{data_show}: nao foi possivel selecionar PIX.')
                screenshot(driver, f'checkout_pix_{data_show.replace("/", "")}')
                return False

        # ── 4. Preencher e-mail do recebedor ─────────────────────────────────
        # O checkout tem um botão "Adicionar e-mail de recebimento" que abre
        # um sheet/modal. Só após preencher o email o botão "Pagar" fica ativo.
        with Cronometro('preencher_email'):
            try:
                # Etapa 4a: clicar no botão "Adicionar e-mail" para abrir o sheet
                btns_email = [
                    b for b in driver.find_elements(By.TAG_NAME, 'button')
                    if b.is_displayed() and any(
                        termo in (b.text or '').lower()
                        for termo in ['e-mail de receb', 'adicionar e-mail', 'recebimento', 'receiver']
                    )
                ]
                if btns_email:
                    driver.execute_script('arguments[0].click();', btns_email[0])
                    log('Clicou no botão de e-mail do recebedor', 'BT')
                    # Aguarda o input de email aparecer (sem sleep fixo)
                    _wait_for(driver, 'input[type="email"]', timeout=3)

                # Etapa 4b: preencher o input de email no sheet
                campos_email = driver.find_elements(
                    By.CSS_SELECTOR,
                    'input[type="email"], input[placeholder*="mail"], input[placeholder*="Mail"], '
                    'input[placeholder*="E-mail"], input[placeholder*="email"]'
                )
                preencheu = False
                for campo in campos_email:
                    try:
                        if campo.is_displayed():
                            campo.clear()
                            campo.send_keys(EMAIL)
                            log('Preencheu e-mail do recebedor', 'BT')
                            preencheu = True
                            break
                    except Exception:
                        continue

                if preencheu:
                    # Etapa 4c: clicar em "Salvar" / "Confirmar" / "Continuar" no sheet
                    time.sleep(0.1)
                    for texto_salvar in ['Salvar', 'Confirmar', 'Continuar', 'OK']:
                        btns_salvar = _btn_por_texto(driver, texto_salvar)
                        for b in btns_salvar:
                            if b.is_displayed() and not b.get_attribute('disabled'):
                                driver.execute_script('arguments[0].click();', b)
                                log(f'Clicou "{texto_salvar}" para confirmar email', 'BT')
                                # Aguarda o sheet fechar (ao invés de sleep fixo)
                                _wait_until_gone(driver, 'input[type="email"]', timeout=2)
                                break
                        else:
                            continue
                        break
            except Exception as e:
                log(f'Email recebedor: aviso ({e}). Continuando...', 'BT')

        # ── 5. Clicar "Pagar" — abre o sheet de cobrança ────────────────────
        with Cronometro('click_pagar'):
            log('Procurando botao Pagar...', 'BT')
            clicou = False
            fim = time.monotonic() + 15
            while time.monotonic() < fim and not clicou:
                btns = [
                    b for b in driver.find_elements(By.TAG_NAME, 'button')
                    if b.is_displayed()
                    and (b.text or '').strip().lower().startswith('pagar')
                    and not b.get_attribute('disabled')
                ]
                for b in btns:
                    driver.execute_script('arguments[0].click();', b)
                    log(f'Clicou "{b.text.strip()[:40]}" via JS', 'BT')
                    clicou = True
                    break
                if not clicou:
                    time.sleep(0.2)

            if not clicou:
                log('Botao Pagar nao encontrado.', 'BT')
                screenshot(driver, f'checkout_sem_pagar_{data_show.replace("/", "")}')
                return False

        # ── 6. Preencher sheet de Informações de cobrança ───────────────────
        # Aguarda o sheet abrir pelo aparecimento do campo CEP (sem sleep fixo)
        _wait_for(driver, 'input[placeholder*="00000"]', timeout=5)
        screenshot(driver, f'checkout_cobranca_{data_show.replace("/", "")}')

        with Cronometro('preencher_cobranca'):
            try:
                # Sem sleep adicional — _wait_for acima já garante que o sheet abriu

                # Usa JS robusto que associa campo pelo texto do <label> parent,
                # pois no novo checkout os inputs não têm placeholder — só label.
                resultado_js = driver.execute_script("""
                    var dados = {
                        'nome completo': arguments[0],
                        'nome': arguments[0],
                        'cpf': arguments[1],
                        'cep': arguments[2],
                        '00000': arguments[2],
                        'n° do endereço': arguments[4],
                        'nº do endereço': arguments[4],
                        'numero do end': arguments[4],
                        'número do end': arguments[4],
                        'número': arguments[4],
                        'endereço': arguments[3],
                        'complemento': arguments[5],
                        'bairro': arguments[6],
                        'cidade': arguments[7],
                        'munic': arguments[7]
                    };

                    function getLabel(inp) {
                        // 1. aria-label
                        var al = inp.getAttribute('aria-label') || '';
                        if (al) return al.toLowerCase();
                        // 2. label[for=id]
                        if (inp.id) {
                            var lbl = document.querySelector('label[for="' + inp.id + '"]');
                            if (lbl) return lbl.textContent.toLowerCase();
                        }
                        // 3. placeholder
                        var ph = inp.placeholder || '';
                        if (ph) return ph.toLowerCase();
                        // 4. sobe ao container e pega o primeiro label
                        var el = inp.parentElement;
                        for (var i = 0; i < 5; i++) {
                            if (!el) break;
                            var lbl = el.querySelector('label');
                            if (lbl) return lbl.textContent.toLowerCase();
                            el = el.parentElement;
                        }
                        return '';
                    }

                    function fill(inp, valor) {
                        inp.focus();
                        var nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        nativeSetter.call(inp, valor);
                        inp.dispatchEvent(new Event('input', {bubbles:true}));
                        inp.dispatchEvent(new Event('change', {bubbles:true}));
                    }

                    var inputs = document.querySelectorAll('input[type="text"], input[type="tel"], input:not([type])');
                    var preenchidos = 0;
                    inputs.forEach(function(inp) {
                        if (inp.disabled || inp.readOnly) return;
                        var label = getLabel(inp);
                        if (!label) return;
                        for (var prefixo in dados) {
                            if (label.indexOf(prefixo) >= 0 && dados[prefixo]) {
                                fill(inp, dados[prefixo]);
                                preenchidos++;
                                break;
                            }
                        }
                    });
                    return preenchidos;
                """,
                    NOME_COMPLETO, CPF, CEP, ENDERECO, NUMERO,
                    COMPLEMENTO or '', BAIRRO, CIDADE
                )
                log(f'Cobrança JS: {resultado_js} campos preenchidos', 'BT')

                # CEP com máscara — digita char-a-char para acionar autocomplete ViaCEP
                try:
                    for sel_cep in ['input[placeholder*="00000"]', 'input[placeholder*="CEP"]',
                                    'input[placeholder*="cep"]']:
                        ceps = driver.find_elements(By.CSS_SELECTOR, sel_cep)
                        if ceps:
                            _digitar_no_campo(driver, ceps[0], CEP, char_a_char=True)
                            log('CEP digitado (aguardando ViaCEP...)', 'BT')
                            # Aguarda ViaCEP preencher endereço (API call ~0.5-1.5s)
                            # Poll a cada 0.2s por até 2s — sai quando Endereço aparecer
                            _wait_for(driver, 'input[placeholder*="00000"]', timeout=0.1)  # ancora rapida
                            time.sleep(1.5)  # ViaCEP precisa de tempo para responder
                            break
                except Exception:
                    pass

                # Estado (UF) via select
                try:
                    from selenium.webdriver.support.ui import Select
                    for sel in driver.find_elements(By.CSS_SELECTOR, 'select'):
                        try:
                            opts = [o.get_attribute('value') for o in sel.find_elements(By.TAG_NAME, 'option')]
                            if ESTADO in opts:
                                Select(sel).select_by_value(ESTADO)
                                log('Estado (UF) selecionado', 'BT')
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

                screenshot(driver, f'cobranca_preenchida_{data_show.replace("/", "")}')

            except Exception as e:
                log(f'Cobrança: erro ({e})', 'BT')

        # ── 7. Clicar "Comprar agora · R$X" ─────────────────────────────────
        with Cronometro('click_comprar_agora_final'):
            log('Procurando botao "Comprar agora"...', 'BT')
            clicou_final = False
            fim = time.monotonic() + 15
            while time.monotonic() < fim and not clicou_final:
                for texto in ['Comprar agora', 'Confirmar', 'Pagar']:
                    try:
                        btns = driver.find_elements(By.TAG_NAME, 'button')
                        for b in btns:
                            try:
                                txt = (b.text or '').strip().lower()
                                if not txt.startswith(texto.lower()):
                                    continue
                                if b.get_attribute('disabled'):
                                    continue
                                if not b.is_displayed():
                                    continue
                                driver.execute_script('arguments[0].click();', b)
                                log(f'Clicou "{b.text.strip()[:40]}" para finalizar', 'BT')
                                clicou_final = True
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass
                    if clicou_final:
                        break
                if not clicou_final:
                    time.sleep(0.1)

            if not clicou_final:
                log('Botao "Comprar agora" nao encontrado — tentando via JS', 'BT')
                try:
                    driver.execute_script("""
                        var btns = document.querySelectorAll('button');
                        for (var b of btns) {
                            var txt = (b.textContent||'').toLowerCase();
                            if (txt.includes('comprar agora') || txt.includes('confirmar')) {
                                if (!b.disabled) { b.click(); break; }
                            }
                        }
                    """)
                    clicou_final = True
                    log('Clicou "Comprar agora" via JS direto', 'BT')
                except Exception:
                    pass

        # ── 8. Aguarda PIX (com retry para erros transitórios de servidor) ────
        screenshot(driver, f'checkout_{data_show.replace("/", "")}')
        resultado = buyticket_aguardar_pix(driver, data_show, setor, categoria, preco)

        # 'erro_pagamento' = servidor rejeitou o pagamento temporariamente.
        # Tenta uma segunda vez após 5s (reabre o sheet de cobrança e resubmete).
        if resultado == 'erro_pagamento':
            log('Retentando checkout após erro de pagamento...', 'BT')
            notificar('Retentar pagamento',
                      f'{data_show}: tentando novamente em 5s...')
            time.sleep(3)
            # Reclica em "Pagar com PIX" para reabrir o sheet de cobrança
            try:
                clicou_pagar = False
                fim_r = time.monotonic() + 10
                while time.monotonic() < fim_r and not clicou_pagar:
                    btns = [b for b in driver.find_elements(By.TAG_NAME, 'button')
                            if b.is_displayed()
                            and (b.text or '').strip().lower().startswith('pagar')
                            and not b.get_attribute('disabled')]
                    for b in btns:
                        driver.execute_script('arguments[0].click();', b)
                        log(f'Retry: Clicou "{b.text.strip()[:30]}" para reabrir cobrança', 'BT')
                        clicou_pagar = True
                        break
                    if not clicou_pagar:
                        time.sleep(0.2)

                if clicou_pagar:
                    # Aguarda sheet reabrir
                    _wait_for(driver, 'input[placeholder*="00000"]', timeout=5)
                    # Repreenche os dados de cobrança
                    resultado_js = driver.execute_script("""
                        var dados = {
                            'nome completo': arguments[0], 'nome': arguments[0],
                            'cpf': arguments[1], 'cep': arguments[2], '00000': arguments[2],
                            'n° do endereço': arguments[4], 'nº do endereço': arguments[4],
                            'numero do end': arguments[4], 'número do end': arguments[4],
                            'número': arguments[4], 'endereço': arguments[3],
                            'complemento': arguments[5], 'bairro': arguments[6],
                            'cidade': arguments[7], 'munic': arguments[7]
                        };
                        function getLabel(inp) {
                            var al = inp.getAttribute('aria-label') || ''; if (al) return al.toLowerCase();
                            if (inp.id) { var lbl = document.querySelector('label[for="'+inp.id+'"]'); if (lbl) return lbl.textContent.toLowerCase(); }
                            var ph = inp.placeholder || ''; if (ph) return ph.toLowerCase();
                            var el = inp.parentElement;
                            for (var i = 0; i < 5; i++) { if (!el) break; var lbl = el.querySelector('label'); if (lbl) return lbl.textContent.toLowerCase(); el = el.parentElement; }
                            return '';
                        }
                        function fill(inp, valor) {
                            inp.focus(); var ns = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; ns.call(inp, valor);
                            inp.dispatchEvent(new Event('input', {bubbles:true})); inp.dispatchEvent(new Event('change', {bubbles:true}));
                        }
                        var inputs = document.querySelectorAll('input[type="text"], input[type="tel"], input:not([type])'); var n = 0;
                        inputs.forEach(function(inp) { if (inp.disabled || inp.readOnly) return; var label = getLabel(inp); if (!label) return; for (var p in dados) { if (label.indexOf(p) >= 0 && dados[p]) { fill(inp, dados[p]); n++; break; } } }); return n;
                    """, NOME_COMPLETO, CPF, CEP, ENDERECO, NUMERO, COMPLEMENTO or '', BAIRRO, CIDADE)
                    log(f'Retry: {resultado_js} campos preenchidos', 'BT')

                    # CEP char-a-char
                    for sel_cep in ['input[placeholder*="00000"]', 'input[placeholder*="CEP"]']:
                        ceps = driver.find_elements(By.CSS_SELECTOR, sel_cep)
                        if ceps:
                            _digitar_no_campo(driver, ceps[0], CEP, char_a_char=True)
                            time.sleep(1.5)
                            break

                    # Estado
                    try:
                        from selenium.webdriver.support.ui import Select
                        for sel in driver.find_elements(By.CSS_SELECTOR, 'select'):
                            opts = [o.get_attribute('value') for o in sel.find_elements(By.TAG_NAME, 'option')]
                            if ESTADO in opts:
                                Select(sel).select_by_value(ESTADO)
                                break
                    except Exception:
                        pass

                    time.sleep(0.5)

                    # Clica "Comprar agora" novamente
                    clicou_r = False
                    fim_btn = time.monotonic() + 15
                    while time.monotonic() < fim_btn and not clicou_r:
                        for b in driver.find_elements(By.TAG_NAME, 'button'):
                            try:
                                txt = (b.text or '').strip().lower()
                                if txt.startswith('comprar agora') and not b.get_attribute('disabled') and b.is_displayed():
                                    driver.execute_script('arguments[0].click();', b)
                                    log('Retry: Clicou "Comprar agora"', 'BT')
                                    clicou_r = True
                                    break
                            except Exception:
                                pass
                        if not clicou_r:
                            time.sleep(0.15)

                    screenshot(driver, f'checkout_retry_{data_show.replace("/", "")}')
                    resultado = buyticket_aguardar_pix(driver, data_show, setor, categoria, preco)
            except Exception as e_retry:
                log(f'Erro no retry de pagamento: {e_retry}', 'BT')

        return resultado

    except Exception as e:
        log(f'Erro no checkout: {e}', 'BT')
        screenshot(driver, 'checkout_erro')
        notificar('Erro no Checkout', f'Erro: {str(e)[:100]}')
        return False


# Campos com jQuery Mask que requerem digitação caractere a caractere
CAMPOS_COM_MASCARA = {'telefone', 'cpf', 'cep'}


def _digitar_no_campo(driver, inp, valor, char_a_char=False):
    """
    Preenche `inp` com `valor`.
    char_a_char=True: necessário para campos com jQuery Mask plugin.
    """
    if char_a_char:
        try:
            inp.click()
        except Exception:
            pass
        driver.execute_script(
            "arguments[0].value = ''; "
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));",
            inp,
        )
        for char in valor:
            inp.send_keys(char)
            time.sleep(0.02)  # jQuery Mask responde em ~10ms — mantido (necessário)
    else:
        driver.execute_script(
            "arguments[0].value = ''; "
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            inp,
        )
        inp.send_keys(valor)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true})); "
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            inp,
        )


def buyticket_preencher_dados(driver):
    """
    Preenche os campos de dados pessoais no checkout.

    Mapeamento por prefixo de placeholder (confirmados no MCP §7).
    IMPORTANTE: não usa id (duplicados no HTML do Bubble).
    """
    notificar_pc('📝 Preenchendo dados', 'Bot preenchendo dados pessoais no checkout...')
    regras = [
        ('e-mail do recebedor', EMAIL),
        ('nome completo', NOME_COMPLETO),
        ('telefone', TELEFONE),
        ('cpf', CPF),
        ('cep', CEP),
        ('estado', ESTADO),
        ('bairro', BAIRRO),
        ('munic', CIDADE),
        ('n°', NUMERO),
        ('nº', NUMERO),
        ('endereço', ENDERECO),
        ('complemento', COMPLEMENTO),
    ]

    preenchidos = 0
    inputs = driver.find_elements(By.CSS_SELECTOR, 'input')
    for inp in inputs:
        try:
            placeholder = (inp.get_attribute('placeholder') or '').strip().lower()
            if not placeholder:
                continue
            for prefixo, valor in regras:
                if placeholder.startswith(prefixo) and valor:
                    usar_char_a_char = prefixo in CAMPOS_COM_MASCARA
                    _digitar_no_campo(driver, inp, valor, char_a_char=usar_char_a_char)
                    # Sem sleep entre campos: preenchimento JS é síncrono (Task 4)
                    preenchidos += 1
                    break
        except Exception:
            continue

    log(f'Dados pessoais preenchidos ({preenchidos} campos)', 'BT')


def _tentar_extrair_pix(driver, src=None):
    """
    Tenta extrair o código PIX por múltiplos métodos.
    Retorna a string do código (começa com 0002) ou None.
    """
    # Estratégia 1: JS scan de todos os elementos de texto
    try:
        code = driver.execute_script("""
            var tags = ['p','span','div','input','textarea','pre','code','li','td'];
            for (var t of tags) {
                var els = document.getElementsByTagName(t);
                for (var el of els) {
                    var txt = (t === 'input' || t === 'textarea')
                        ? (el.value || '') : (el.textContent || '');
                    txt = txt.trim();
                    if (txt.startsWith('00020') && txt.length > 50) {
                        return txt;
                    }
                }
            }
            return '';
        """)
        if code and len(code) > 50:
            return code
    except Exception:
        pass

    # Estratégia 2: input disabled/readonly com value
    try:
        for inp in driver.find_elements(By.CSS_SELECTOR, 'input[disabled], input[readonly]'):
            val = inp.get_attribute('value') or ''
            if val.startswith('0002') and len(val) > 30:
                return val
    except Exception:
        pass

    # Estratégia 3: regex no page_source (captura código em JSON ou atributos)
    if src:
        try:
            m = re.search(r'(0002\S{30,})', src)
            if m:
                return m.group(1)
        except Exception:
            pass

    return None


def _aguardar_conclusao_manual(driver, data_show, setor, categoria, preco, timeout_min=15):
    """
    Deixa a página ABERTA e aguarda o usuário finalizar o pagamento manualmente.

    Chamado quando o bot não consegue selecionar cartão de crédito ou parcelas.
    Fica em loop monitorando a URL/source por até `timeout_min` minutos.
    Retorna True se detectar confirmação, ou 'manual_timeout' se o prazo esgotar.
    """
    log(f'[MANUAL] Aguardando conclusão manual do pagamento ({timeout_min}min)...', 'BT')
    notificar(
        '⚠️ AÇÃO MANUAL NECESSÁRIA',
        f'{data_show}: bot não finalizou pagamento automático.\n'
        f'Página ABERTA — selecione Cartão de Crédito, escolha 3x e clique Comprar agora.\n'
        f'Aguardando {timeout_min} minutos.',
        'urgent',
    )
    notificar_pc(
        '⚠️ PAGAMENTO MANUAL NECESSÁRIO',
        f'Finalize no navegador: {setor}/{categoria} R${preco}',
        'urgent',
    )
    screenshot(driver, f'manual_necessario_{data_show.replace("/", "")}')

    url_inicial = driver.current_url
    fim = time.monotonic() + timeout_min * 60

    _CONFIRMACAO_KW = [
        'confirmado', 'aprovado', 'sucesso', 'obrigado', 'thank',
        'pedido realizado', 'pagamento realizado', 'compra realizada',
        'ingresso emitido', 'pedido confirmado',
    ]

    while time.monotonic() < fim:
        time.sleep(5)
        try:
            url_atual = driver.current_url
            src_l     = driver.page_source.lower()

            # Detecta confirmação via conteúdo da página
            if any(kw in src_l for kw in _CONFIRMACAO_KW):
                log('[MANUAL] Confirmação de pagamento detectada!', 'BT')
                notificar(
                    '✅ PAGAMENTO CONFIRMADO (manual)',
                    f'{data_show} {setor}/{categoria} R${preco} — compra realizada!',
                    'urgent',
                )
                screenshot(driver, f'confirmado_manual_{data_show.replace("/", "")}')
                return True

            # Detecta redirecionamento pós-pagamento
            if url_atual != url_inicial and any(
                kw in url_atual.lower()
                for kw in ['confirmacao', 'sucesso', 'obrigado', 'thank', 'pedido']
            ):
                log(f'[MANUAL] URL mudou para confirmação: {url_atual[:60]}', 'BT')
                notificar(
                    '✅ COMPRA CONFIRMADA (manual)',
                    f'{data_show}: redirecionado para {url_atual[:80]}',
                    'urgent',
                )
                screenshot(driver, f'confirmado_url_{data_show.replace("/", "")}')
                return True

        except Exception as e:
            log(f'[MANUAL] Erro ao monitorar: {e}', 'BT')

    log(f'[MANUAL] Timeout de {timeout_min}min — retomando monitoramento.', 'BT')
    notificar(
        'Timeout pagamento manual',
        f'{data_show}: {timeout_min}min sem confirmação. Bot retomando.',
    )
    return 'manual_timeout'


def buyticket_aguardar_confirmacao_cartao(driver, data_show, setor, categoria, preco):
    """
    Aguarda a confirmação de pagamento após clique em "Comprar agora" com cartão.

    O gateway de cartão costuma redirecionar para uma tela de confirmação em
    poucos segundos. Polling a cada 2s por até 120s. Se não detectar confirmação,
    entrega para conclusão manual via _aguardar_conclusao_manual().
    """
    log('Aguardando confirmação de pagamento (cartão de crédito)...', 'BT')
    notificar('Processando pagamento', f'{data_show}: cartão em processamento...')

    _CONFIRMACAO_KW = [
        'confirmado', 'aprovado', 'sucesso', 'obrigado', 'thank you',
        'pedido realizado', 'pagamento realizado', 'compra realizada',
        'ingresso emitido', 'pedido confirmado',
    ]
    _RECUSA_KW = [
        'recusado', 'negado', 'não aprovado', 'nao aprovado',
        'cartão inválido', 'limite insuficiente', 'tente novamente',
        'nao conseguimos processar', 'não conseguimos processar',
    ]

    fim = time.monotonic() + 120
    while time.monotonic() < fim:
        time.sleep(2)
        try:
            src_l = driver.page_source.lower()

            if any(kw in src_l for kw in _CONFIRMACAO_KW):
                log('Pagamento aprovado pelo gateway!', 'BT')
                notificar(
                    '✅ PAGAMENTO APROVADO',
                    f'{data_show} {setor}/{categoria} R${preco} — compra confirmada!',
                    'urgent',
                )
                screenshot(driver, f'cartao_aprovado_{data_show.replace("/", "")}')
                return True

            if any(kw in src_l for kw in _RECUSA_KW):
                log('Cartão recusado pelo gateway.', 'BT')
                notificar(
                    'Cartão recusado',
                    f'{data_show}: gateway recusou o pagamento. Verifique o cartão.',
                    'high',
                )
                screenshot(driver, f'cartao_recusado_{data_show.replace("/", "")}')
                return False

        except Exception as e:
            log(f'aguardar_confirmacao_cartao: {e}', 'BT')

    # Gateway não respondeu em 120s — entrega para o usuário
    log('Timeout de 120s aguardando gateway. Abrindo para conclusão manual.', 'BT')
    return _aguardar_conclusao_manual(driver, data_show, setor, categoria, preco)


def buyticket_aguardar_pix(driver, data_show, setor, categoria, preco):
    """
    Aguarda o código PIX aparecer na página e monitora até o tempo acabar.

    Novo layout Next.js (set/2026 / out/2026):
      - Tela de sucesso: "Aguardando pagamento" + "Tempo para pagar" + código PIX
      - Polling a cada 2s, máximo 120s para a tela aparecer.
      - Extração: JS scan de todos os elementos de texto (robusto a CSS ellipsis).
      - Erros transitórios ("Não conseguimos processar") → retorna 'erro_pagamento'.
    """
    log('Aguardando código PIX...', 'BT')
    notificar('Aguardando PIX', 'Compra em andamento, aguardando codigo PIX...')

    with Cronometro('aguardar_pix'):
        # ── Palavras-chave EXCLUSIVAS da tela de sucesso PIX ────────────────
        # NÃO incluir "código pix" — aparece também no checkout (método de pag.)
        _SUCESSO_KW = ['aguardando pagamento', 'tempo para pagar']
        _ERRO_KW = [
            'não conseguimos processar', 'nao conseguimos processar',
            'algo deu errado', 'tente de novo',
        ]

        # ── Fase 1: polling até 120s aguardando tela do PIX ─────────────────
        fim_espera = time.monotonic() + 120
        codigo_pix = None
        na_tela_pix = False

        while time.monotonic() < fim_espera:
            time.sleep(1)  # polling mais agressivo para detectar PIX mais rápido
            try:
                src = driver.page_source
                src_l = src.lower()

                # Verifica se estamos na tela de sucesso PIX
                if any(kw in src_l for kw in _SUCESSO_KW):
                    na_tela_pix = True
                    log('Tela PIX detectada. Extraindo código...', 'BT')
                    # Aguarda até 15s para o código aparecer no DOM (latência da API)
                    for tentativa in range(15):
                        codigo_pix = _tentar_extrair_pix(driver, src)
                        if codigo_pix:
                            log(f'PIX extraído (tentativa {tentativa+1}, len={len(codigo_pix)})', 'BT')
                            break
                        time.sleep(1)
                        src = driver.page_source  # atualiza src para regex
                    break  # sai do loop principal independente de ter extraído ou não

                # Verifica erro de pagamento (servidor rejeitou)
                if any(e in src_l for e in _ERRO_KW):
                    log('Erro no processamento do pagamento (servidor). Abortando.', 'BT')
                    screenshot(driver, f'pag_erro_{data_show.replace("/", "")}')
                    notificar('Erro no pagamento',
                              f'{data_show}: servidor recusou o pagamento. Retentando...')
                    return 'erro_pagamento'

            except Exception as ex:
                log(f'Fase 1 aguardar_pix erro: {ex}', 'BT')

        if not na_tela_pix:
            log('Timeout: tela PIX nao apareceu em 120s.', 'BT')
            notificar('Timeout PIX', f'{data_show}: PIX nao gerado em 120s.')
            screenshot(driver, 'pix_timeout')
            return 'expirado'

        # ── Fase 2: extração falhou mesmo após 15 tentativas ────────────────
        if not codigo_pix:
            log('Nao foi possivel extrair o codigo PIX da tela.', 'BT')
            notificar('Erro PIX', f'{data_show}: tela PIX visivel mas codigo nao extraido.')
            screenshot(driver, f'pix_sem_codigo_{data_show.replace("/", "")}')
            return 'expirado'

        # ── Fase 3: PIX encontrado — notifica ───────────────────────────────
        log(f'CODIGO PIX ENCONTRADO! {codigo_pix[:80]}...', 'BT')
        log(f'PIX_COMPLETO: {codigo_pix}', 'PIX')
        notificar(
            'PIX GERADO - PAGUE AGORA!',
            f'Ingresso: {setor} / {categoria}\n'
            f'Show: {data_show}\n'
            f'Valor: R${preco}\n'
            f'Codigo PIX: {codigo_pix}',
            'urgent',
        )
        notificar('CODIGO PIX - COPIE E PAGUE', codigo_pix, 'urgent')
        screenshot(driver, f'pix_sucesso_{data_show.replace("/", "")}')

        # ── Fase 4: monitora countdown "Tempo para pagar" ───────────────────
        log('Mantendo tela do PIX aberta. Monitorando countdown...', 'BT')
        url_pix = driver.current_url
        fim_maximo = time.monotonic() + 660  # 11 min de segurança absoluta

        while time.monotonic() < fim_maximo:
            time.sleep(3)
            try:
                url_atual = driver.current_url
                if url_atual != url_pix and 'pix' not in url_atual.lower():
                    log('URL mudou — possivel confirmacao de pagamento!', 'BT')
                    notificar('Pagamento confirmado?',
                              f'{data_show}: tela mudou apos PIX. Verifique!', 'urgent')
                    screenshot(driver, f'pix_confirmado_{data_show.replace("/", "")}')
                    return True

                # Timer "Tempo para pagar: MM:SS"
                timers = driver.find_elements(
                    By.CSS_SELECTOR,
                    '[class*="countdown"],[class*="timer"],[class*="tempo"],[class*="clock"]'
                )
                if not timers:
                    todos = driver.find_elements(By.CSS_SELECTOR, '*')
                    timers = [
                        t for t in todos
                        if re.match(r'^\d{1,2}:\d{2}$', (t.text or '').strip())
                    ]

                for t in timers:
                    txt = (t.text or '').strip()
                    m = re.match(r'^(\d{1,2}):(\d{2})$', txt)
                    if m:
                        restante = int(m.group(1)) * 60 + int(m.group(2))
                        log(f'Timer PIX: {txt} ({restante}s restantes)', 'BT')
                        if restante <= 30:
                            notificar('PIX EXPIRANDO!',
                                      f'{data_show}: menos de 30s para o PIX expirar!',
                                      'urgent')
                        if restante == 0:
                            log('Timer zerou — PIX expirado.', 'BT')
                            notificar('PIX Expirado',
                                      f'{data_show}: PIX expirou. Tentando novo ingresso...')
                            screenshot(driver, f'pix_expirado_{data_show.replace("/", "")}')
                            return 'expirado'
                        break

                # PIX sumiu da tela
                if '0002' not in driver.page_source:
                    log('PIX sumiu da tela — provavel expiracao.', 'BT')
                    notificar('PIX Expirado',
                              f'{data_show}: codigo PIX sumiu.')
                    screenshot(driver, f'pix_sumiu_{data_show.replace("/", "")}')
                    return 'expirado'

            except Exception as e:
                log(f'Erro ao monitorar PIX: {e}', 'BT')

    log('Timeout maximo (11min) monitorando PIX.', 'BT')
    notificar('Timeout PIX', f'{data_show}: tempo maximo excedido.')


# ============================================================
# BUYTICKET — MONITORAMENTO
# ============================================================


def _checar_login_periodico(driver, ultima_checagem_login):
    """Verifica periodicamente se a sessão está ativa; re-loga se caiu.

    Se o bot estiver na fila do CrowdHandler, o check é ignorado para não
    arriscar navegar para o login e perder a posição na fila.
    """
    agora = time.monotonic()
    if not deve_checar_login(agora, ultima_checagem_login, INTERVALO_LOGIN):
        return ultima_checagem_login

    if _em_fila_de_espera(driver):
        log('Check de login ignorado: bot esta na fila de espera.', 'BT')
        return ultima_checagem_login

    log(f'Verificando sessao (padrao de tempo: {INTERVALO_LOGIN}s)...', 'BT')
    if not buyticket_sessao_ativa(driver):
        log('Sessao caiu - refazendo login...', 'BT')
        notificar('BuyTicket Sessao', 'Login caiu, refazendo autenticacao...')
        buyticket_login(driver)
    return time.monotonic()


def _abrir_aba_dia(driver, url, data_show):
    """
    Navega para a URL de sessão do dia e aguarda o ComboBox aparecer.
    Com URLs de sessão fixas (set/2026), não precisa mais clicar em botão de data.
    Retorna o handle da aba se abriu com sucesso, None em caso de erro.
    """
    try:
        driver.get(url)
        _fechar_popup_aviso(driver)
        # Âncora: ComboBox confirma que a sessão está ativa
        ancora = _wait_for(driver, _SEL_COMBOBOX_TIPO, timeout=15)
        if ancora:
            log(f'Aba do dia {data_show} aberta (sessao ativa)', 'BT')
            return driver.window_handles[0]
        # Fallback: âncora genérica (pode estar em fila ou carregando lento)
        ancora2 = _wait_for(driver, _SEL_ANCORA_EVENTO, timeout=5)
        if ancora2 or True:
            log(f'Aba do dia {data_show} aberta', 'BT')
            return driver.window_handles[0]
    except Exception as e:
        log(f'Erro ao abrir aba do dia {data_show}: {e}', 'BT')
    return None


def _monitor_dia(data_show, url):
    """
    Monitora e compra ingressos para UM único dia de show, com driver próprio.
    Thread independente por dia (arquitetura paralela).
    """
    log(f'[{data_show}] Thread de monitoramento iniciada', 'BT')

    driver = None
    ultima_checagem_login = None
    preco_cache = None

    while True:
        try:
            if driver is None:
                driver = criar_driver()
                if not BUYTICKET_MODO_MONITOR:
                    buyticket_login(driver)
                else:
                    log(f'[{data_show}] Modo monitor (sem login) — abrindo evento publicamente', 'BT')
                aba = _abrir_aba_dia(driver, url, data_show)
                if not aba:
                    log(f'[{data_show}] Falha ao abrir aba. Tentando de novo em 10s...', 'BT')
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = None
                    time.sleep(10)
                    continue
                ultima_checagem_login = time.monotonic()
                preco_cache = None

            if not BUYTICKET_MODO_MONITOR:
                ultima_checagem_login = _checar_login_periodico(driver, ultima_checagem_login)

            try:
                if _em_fila_de_espera(driver):
                    log(f'[{data_show}] Em fila — aguardando liberar (sem recarregar)...', 'BT')
                    _aguardar_sair_da_fila(driver)

                _refresh_mantendo_dia(driver, url)

                # ── Verificação: bot está no site certo? ─────────────────────
                # Detecta e alerta se a aba foi parar em outro domínio
                # (ex: Ticketmaster) sem que o usuário saiba.
                if not _verificar_site_correto(driver, data_show):
                    # Tenta corrigir navegando de volta para a URL correta
                    log(f'[{data_show}] Tentando voltar para a URL correta...', 'BT')
                    try:
                        driver.get(url)
                        time.sleep(3)
                    except Exception:
                        pass
                    time.sleep(INTERVALO_CHECK)
                    continue

                preco_botao = buyticket_ler_preco_botao(driver)

                if preco_botao is None:
                    if preco_cache is not None:
                        log(f'[{data_show}] Preco sumiu (era R${preco_cache}). Recarregando...', 'BT')
                        _refresh_mantendo_dia(driver, url)
                        preco_cache = None
                    time.sleep(INTERVALO_CHECK + random.uniform(0, INTERVALO_CHECK))
                    continue

                if preco_botao > MAIOR_LIMITE:
                    if preco_botao != preco_cache:
                        log(f'[{data_show}] Preco: R${preco_botao} (fora do limite R${MAIOR_LIMITE})', 'BT')
                        preco_cache = preco_botao
                    time.sleep(INTERVALO_CHECK + random.uniform(0, INTERVALO_CHECK))
                    continue

                if preco_botao != preco_cache:
                    log(f'[{data_show}] Preco: R${preco_botao} (era R${preco_cache}) — DENTRO DO LIMITE!', 'BT')
                    preco_cache = preco_botao

                # ── Modo monitor: notifica mas não compra ────────────────────
                if BUYTICKET_MODO_MONITOR:
                    if preco_botao != preco_cache or preco_cache is None:
                        log(f'[{data_show}] R${preco_botao} DISPONIVEL! (modo monitor — sem credenciais para comprar)', 'BT')
                        notificar(
                            f'Ingresso Disponivel! {data_show}',
                            f'R${preco_botao} dentro da faixa!\n'
                            f'Preencha BUYTICKET_CPF e BUYTICKET_SENHA no .env para comprar automaticamente.',
                            'urgent',
                        )
                    time.sleep(INTERVALO_CHECK)
                    continue

                log(f'[{data_show}] R${preco_botao} dentro da faixa! Comprando...', 'BT')
                notificar(
                    f'Ingresso! {data_show}',
                    f'R${preco_botao} dentro da faixa. Comprando...',
                    'high',
                )

                _refresh_mantendo_dia(driver, url)
                preco_cache = None

                sucesso = buyticket_fluxo_compra(driver, data_show, url)
                if sucesso is True:
                    log(f'[{data_show}] COMPRA CONCLUIDA!', 'BT')
                    notificar('COMPRA CONCLUIDA!',
                              f'Ingresso {data_show}! Verifique o PIX!', 'urgent')
                elif sucesso == 'expirado':
                    log(f'[{data_show}] PIX expirou. Voltando ao monitoramento...', 'BT')
                    preco_cache = None
                else:
                    log(f'[{data_show}] Compra nao concluida. Monitorando...', 'BT')
                    preco_cache = None

            except InvalidSessionIdException as e:
                log(f'[{data_show}] Sessao morta — reiniciando driver... ({e})', 'BT')
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
                continue

            except Exception as e:
                log(f'[{data_show}] Erro: {e}', 'BT')
                preco_cache = None

            time.sleep(INTERVALO_CHECK + random.uniform(0, INTERVALO_CHECK))

        except Exception as e:
            log(f'[{data_show}] Erro fatal — reiniciando em 5s... {e}', 'BT')
            log(traceback.format_exc(), 'BT')
            notificar('Erro BuyTicket',
                      f'[{data_show}] Browser crashou, reiniciando... {str(e)[:80]}')
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass
            driver = None
            time.sleep(5)


def monitor_buyticket():
    """
    Orquestrador do monitoramento BuyTicket.
    Dispara uma thread por dia de show (arquitetura paralela).
    """
    log('Iniciando monitor BuyTicket (modo paralelo por dia)...', 'BT')
    notificar('Monitor BuyTicket',
              f'Monitor iniciado: {len(DATAS_SHOW)} threads paralelas ({", ".join(DATAS_SHOW)})')

    threads = []
    for i, data_show in enumerate(DATAS_SHOW):
        url = URLS_BUYTICKET[i] if i < len(URLS_BUYTICKET) else URLS_BUYTICKET[0]
        t = threading.Thread(
            target=_monitor_dia,
            args=(data_show, url),
            daemon=True,
            name=f'BT-{data_show}',
        )
        threads.append(t)
        time.sleep(3)  # escalonamento: evita 3 logins simultâneos
        t.start()
        log(f'Thread {data_show} iniciada', 'BT')

    for t in threads:
        t.join()


# ============================================================
# TICKETMASTER — AUTENTICAÇÃO
# ============================================================





# Arquivo para persistir cookies do Ticketmaster entre sessoes.
# Evita refazer login (e enfrentar o Cloudflare) a cada reinicio.
TM_COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tm_cookies.json")


def _tm_salvar_cookies(driver):
    """Salva os cookies da sessao TM atual em disco."""
    try:
        cookies = driver.get_cookies()
        with open(TM_COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
        log(f"[TM] Cookies salvos ({len(cookies)} cookies)", "TM")
    except Exception as e:
        log(f"[TM] Erro ao salvar cookies: {e}", "TM")


def _tm_carregar_cookies(driver):
    """
    Carrega cookies salvos e verifica se a sessao ainda e valida.
    Retorna True se a sessao foi restaurada com sucesso.
    """
    if not os.path.exists(TM_COOKIES_FILE):
        log("[TM] Sem cookies salvos", "TM")
        return False
    try:
        with open(TM_COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        # Navega para o dominio antes de injetar cookies
        driver.get(TICKETMASTER_URL_HOME)
        _wait_for(driver, "body", timeout=8)
        for cookie in cookies:
            # Remove campos invalidos para add_cookie
            cookie.pop("sameSite", None)
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass
        log(f"[TM] Cookies carregados ({len(cookies)} cookies) ? verificando sessao...", "TM")
        driver.refresh()
        _wait_for(driver, "body", timeout=8)
        # Verifica se esta logado (botao Entrar/Cadastre-se ausente)
        src_low = driver.page_source.lower()
        logado = "entrar / cadastre-se" not in src_low
        if logado:
            log("[TM] Sessao restaurada via cookies!", "TM")
            return True
        log("[TM] Cookies expirados ? necessario novo login", "TM")
        os.remove(TM_COOKIES_FILE)
        return False
    except Exception as e:
        log(f"[TM] Erro ao carregar cookies: {e}", "TM")
        return False


def _tm_aceitar_cookies(driver):
    """Fecha o banner de cookies do Ticketmaster clicando em 'Aceitar todos os cookies'."""
    try:
        btns = driver.find_elements(By.CSS_SELECTOR, 'button')
        for btn in btns:
            if 'aceitar todos' in (btn.text or '').strip().lower() and btn.is_displayed():
                btn.click()
                import time as _t; _t.sleep(0.8)
                log('[TM] Banner de cookies aceito', 'TM')
                return
    except Exception:
        pass


def _tm_clicar_por_texto(driver, texto):
    """
    Clica no primeiro <button> ou <a> visivel cujo texto contem 	exto.
    Necessario porque o Ticketmaster usa <a> para 'Entrar / Cadastre-se'.
    Retorna True se clicou, False se nao encontrou.
    """
    alvo = texto.strip().lower()
    try:
        elementos = driver.find_elements(By.CSS_SELECTOR, 'button, a')
        for el in elementos:
            try:
                txt = (el.text or '').strip().lower()
                if alvo in txt and el.is_displayed():
                    el.click()
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False



def _tm_resolver_captcha(driver):
    """
    Clica no checkbox do Cloudflare Turnstile (dentro do iframe) e aguarda
    resolucao (token preenchido ou estado "Sucesso" no iframe).

    Estrategia dupla de deteccao do iframe:
      1. Por atributo src (cloudflare/turnstile/challenges)
      2. Por conteudo (switch + page_source) ? usado quando o iframe ainda
         nao tem src definido (carregamento assincrono)

    Retorna True quando resolvido ou quando nao ha captcha, False se timeout.
    """
    iframe = None

    # Tentativa 1: por src URL
    try:
        for ifr in driver.find_elements(By.TAG_NAME, "iframe"):
            s = (ifr.get_attribute("src") or "").lower()
            if any(kw in s for kw in ("cloudflare", "turnstile", "challenges", "cf-")):
                iframe = ifr
                break
    except Exception:
        pass

    # Tentativa 2: por conteudo (iframe sem src ou src ainda vazio)
    if not iframe:
        try:
            for ifr in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(ifr)
                    content = driver.page_source.lower()
                    driver.switch_to.default_content()
                    if any(kw in content for kw in
                           ("confirme", "humano", "turnstile", "cloudflare", "verificando")):
                        iframe = ifr
                        break
                except Exception:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception:
            pass

    if not iframe:
        log("[TM] Iframe Cloudflare nao encontrado (assumindo auto-pass)", "TM")
        return True

    # Clica no checkbox dentro do iframe
    try:
        driver.switch_to.frame(iframe)
        clicou_cf = False
        for sel in ["input[type='checkbox']", "label", "div[role='checkbox']", "body"]:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    els[0].click()
                    clicou_cf = True
                    break
            except Exception:
                pass
        if clicou_cf:
            log("[TM] Cloudflare checkbox clicado", "TM")
    except Exception as e:
        log(f"[TM] Erro ao clicar captcha iframe: {e}", "TM")
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass

    # Aguarda resolucao (poll por token no DOM ou sucesso no iframe)
    fim = time.monotonic() + 12
    while time.monotonic() < fim:
        try:
            # 1. Checa token no DOM principal
            token = driver.execute_script("""
                var els = document.querySelectorAll(
                    "[name='cf-turnstile-response'],[name*='turnstile'],[id*='turnstile']"
                );
                for (var e of els) {
                    if (e.value && e.value.length > 20) return "ok";
                }
                return "";
            """)
            if token:
                log("[TM] Cloudflare resolvido (token no DOM)", "TM")
                return True
        except Exception:
            pass

        try:
            # 2. Checa estado dentro do iframe
            driver.switch_to.frame(iframe)
            texto = driver.execute_script(
                "return (document.body && document.body.innerText || '').toLowerCase()"
            )
            driver.switch_to.default_content()
            if "sucesso" in texto or "success" in texto:
                log("[TM] Cloudflare resolvido (sucesso no iframe)", "TM")
                return True
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

        time.sleep(0.6)

    log("[TM] Cloudflare nao resolveu em 12s", "TM")
    return False

def ticketmaster_login(driver):
    """
    Faz login no Ticketmaster.

    Estrategia:
    1. Tenta carregar cookies salvos em tm_cookies.json (sessao anterior).
    2. Se cookies validos -> skip do login, monitoramento inicia imediatamente.
    3. Se sem cookies -> monitora sem autenticacao (paginas publicas).

    Para adicionar login manual:
      a) No Chrome normal, logue no Site B
      b) Instale a extensao "Cookie-Editor"
      c) Exporte os cookies como JSON
      d) Salve como tm_cookies.json nesta pasta
      O bot carregara automaticamente na proxima execucao.

    Nota: o Cloudflare Turnstile bloqueia login automatizado via Selenium.
    """
    log("Verificando autenticacao Ticketmaster...", "TM")

    # Tenta restaurar sessao via cookies salvos
    if _tm_carregar_cookies(driver):
        notificar("TM Login OK", "Sessao Ticketmaster restaurada via cookies!")
        return

    # Sem cookies: informa e continua sem autenticacao
    log("[TM] Sem cookies de sessao. Monitorando sem autenticacao.", "TM")
    log("[TM] Para autenticar: exporte cookies do Chrome normal -> tm_cookies.json", "TM")


def ticketmaster_sessao_ativa(driver):
    """
    Verifica se o login Ticketmaster ainda está ativo.

    Abre aba auxiliar e navega à homepage. O indicador de sessão é o botão
    "Entrar / Cadastre-se" no cabeçalho:
      - Visível  → não está logado → return False
      - Ausente  → usuário autenticado (avatar/nome no lugar) → return True

    Encerra a aba auxiliar em finally e restaura o handle original.
    """
    aba_original = driver.current_window_handle
    driver.execute_script("window.open('')")
    abas = driver.window_handles
    driver.switch_to.window(abas[-1])

    caiu = True  # assume caiu até provar o contrário
    try:
        driver.get(URL_TM_HOME)
        _wait_for(driver, 'body', timeout=8)

        # Procura o botão "Entrar / Cadastre-se" ou "Entrar" no header
        todos = driver.find_elements(By.CSS_SELECTOR, 'button, a')
        botao_entrar_visivel = False
        for el in todos:
            try:
                txt = el.text.strip().lower()
                if ('entrar' in txt and 'cadastre' in txt) or txt == 'entrar / cadastre-se':
                    if el.is_displayed():
                        botao_entrar_visivel = True
                        break
            except Exception:
                pass
        caiu = botao_entrar_visivel

    except Exception as e:
        log(f'[TM] Erro ao checar sessao TM: {e}', 'TM')
        caiu = False  # inconclusivo → assume ativo
    finally:
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(aba_original)
        except Exception:
            pass

    if caiu:
        log('[TM] Sessao TM CAIU — botao Entrar/Cadastre-se visivel', 'TM')
        return False

    log('[TM] Sessao TM ativa', 'TM')
    return True


def _checar_login_periodico_tm(driver, ultima_checagem_login):
    """Verifica periodicamente se a sessão TM está ativa; re-loga se caiu."""
    agora = time.monotonic()
    if not deve_checar_login(agora, ultima_checagem_login, INTERVALO_LOGIN):
        return ultima_checagem_login

    log(f'[TM] Verificando sessao TM (padrao de tempo: {INTERVALO_LOGIN}s)...', 'TM')
    if not ticketmaster_sessao_ativa(driver):
        log('[TM] Sessao TM caiu - refazendo login...', 'TM')
        notificar('Ticketmaster Sessao', 'Login TM caiu, refazendo autenticacao...')
        ticketmaster_login(driver)
    else:
        log('[TM] Sessao TM ativa', 'TM')
    return time.monotonic()


# ============================================================
# TICKETMASTER — ERROS DE REDE (helpers)
# ============================================================

# Substrings que identificam erros TRANSITÓRIOS de rede no Chrome.
# Esses erros NÃO indicam crash do browser — o driver está vivo.
# Ocorrem tipicamente após standby, troca de rede ou perda momentânea.
_TM_ERROS_REDE_KW = (
    'err_name_not_resolved',
    'err_internet_disconnected',
    'err_network_changed',
    'err_connection_reset',
    'err_connection_refused',
    'err_connection_timed_out',
    'err_timed_out',
    'err_address_unreachable',
    'net::err_',        # apanha qualquer net::ERR_ do Chrome
)


def _e_erro_de_rede(exc: Exception) -> bool:
    """
    Retorna True quando a exceção é um erro transitório de rede.
    Erros de rede NÃO devem matar o driver nem gerar notificação de alarme —
    basta aguardar a conectividade ser restaurada e continuar.
    """
    msg = str(exc).lower()
    return any(kw in msg for kw in _TM_ERROS_REDE_KW)


def _aguardar_rede(
    host: str = '',
    timeout_s: int = 180,
    intervalo_s: int = 8,
    prefixo_log: str = 'TM',
) -> bool:
    """
    Aguarda até que a resolução DNS do host seja bem-sucedida (rede disponível).

    Faz polling a cada `intervalo_s` segundos até `timeout_s` segundos.
    Retorna True quando a rede está up, False se atingiu o timeout.

    Usado após qualquer `ERR_NAME_NOT_RESOLVED` ou erro de rede para
    não fazer o Chrome tentar navegar antes da interface de rede reconectar.
    """
    host = host or os.getenv('TICKETMASTER_DOMINIO', 'www.ticketmaster.com.br')
    fim = time.monotonic() + timeout_s
    tentativa = 0
    while time.monotonic() < fim:
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            if tentativa > 0:
                log(f'Rede restaurada apos {tentativa * intervalo_s}s de espera.', prefixo_log)
            return True
        except socket.gaierror:
            tentativa += 1
            restante = int(fim - time.monotonic())
            log(
                f'Aguardando rede... tentativa {tentativa} '
                f'(timeout em {restante}s)',
                prefixo_log,
            )
            time.sleep(intervalo_s)
    log(f'Timeout {timeout_s}s aguardando rede. Prosseguindo mesmo assim.', prefixo_log)
    return False


# ============================================================
# TICKETMASTER — MONITORAMENTO
# ============================================================

# Estado por dia: None (desconhecido), 'esgotado' ou 'disponivel'.
# Notificação urgente só é enviada quando muda de não-disponivel → disponivel,
# evitando spam a cada ciclo de polling.
_ESTADO_TM: dict = {}

# Palavras que indicam ingresso ESGOTADO/indisponível no Ticketmaster.
_TM_ESGOTADO_KW = [
    'esgotado', 'sold out', 'indisponível', 'indisponivel',
    'não disponível', 'nao disponivel', 'ingressos esgotados',
    'this event is sold out',
]

# Seletores de botão de compra ATIVO no Ticketmaster (sem [disabled]).
_TM_BTN_COMPRA_SEL = (
    "button:not([disabled])[class*='buy'], "
    "button:not([disabled])[data-testid*='buy'], "
    "button:not([disabled])[data-testid*='cart'], "
    "a[href*='/cart'], a[href*='/checkout'], "
    "button:not([disabled])[class*='purchase'], "
    "button:not([disabled])[class*='ticket']"
)


def _tm_tem_botao_ativo(driver) -> bool:
    """
    Retorna True se há um botão de compra clicável visível na página.
    Usa seletores Ticketmaster + varredura de texto em botões visíveis.
    """
    # 1. Seletores estruturais específicos
    try:
        els = driver.find_elements(By.CSS_SELECTOR, _TM_BTN_COMPRA_SEL)
        if any(e.is_displayed() for e in els):
            return True
    except Exception:
        pass

    # 2. Varredura de texto em qualquer botão/link visível
    try:
        candidatos = driver.find_elements(
            By.CSS_SELECTOR,
            "button:not([disabled]), a[role='button']",
        )
        kw_compra = ('comprar', 'adicionar', 'add to cart', 'buy', 'compre', 'garantir')
        for el in candidatos:
            if not el.is_displayed():
                continue
            txt = el.text.strip().lower()
            if any(k in txt for k in kw_compra):
                return True
    except Exception:
        pass

    return False


def _tm_classificar(driver, data_show: str) -> str:
    """
    Classifica o estado atual da página:
      'esgotado'   – palavras-chave de esgotado visíveis
      'disponivel' – botão de compra ativo encontrado
      'desconhecido' – nenhum sinal claro
    """
    page_lower = driver.page_source.lower()

    if any(kw in page_lower for kw in _TM_ESGOTADO_KW):
        return 'esgotado'

    if _tm_tem_botao_ativo(driver):
        return 'disponivel'

    # Fallback: tabela de preços visível + nenhum "esgotado" = possível disponível
    precos = re.findall(r'R\$\s*[\d.,]+', driver.page_source)
    if len(precos) >= 3:
        # Preços na tabela informativa — NÃO garante compra ativa.
        # Registra como desconhecido para não spammar.
        log(f'[{data_show}] TM: tabela de precos visivel mas sem botao ativo', 'TM')
        return 'desconhecido'

    return 'desconhecido'


def ticketmaster_tentar_comprar(driver, data_show, url):
    """
    Tenta comprar um ingresso no Ticketmaster assim que o estado muda para
    'disponivel'. Fluxo:

      1. Clica no botão de compra da página atual.
      2. Aguarda o checkout carregar.
      3. Tenta selecionar Cartão de Crédito.
      4. Tenta selecionar 3x parcelas.
      5. Preenche dados pessoais (mesmos do BuyTicket).
      6. Clica em Finalizar / Confirmar.
      7. Aguarda confirmação do gateway.
      → Se qualquer etapa falhar: página fica ABERTA para conclusão manual.
    """
    log(f'[TM] Tentando comprar ingresso {data_show}...', 'TM')
    notificar('TM Iniciando compra', f'{data_show}: tentando comprar automaticamente...', 'urgent')
    screenshot(driver, f'tm_compra_inicio_{data_show.replace("/", "")}')

    # ── 1. Clicar no botão de compra ────────────────────────────────────────
    clicou_compra = False
    try:
        # Seletores estruturais TM
        for sel in [_TM_BTN_COMPRA_SEL, "button:not([disabled])", "a[role='button']"]:
            candidatos = driver.find_elements(By.CSS_SELECTOR, sel)
            kw = ('comprar', 'adicionar', 'add to cart', 'buy', 'compre', 'garantir', 'selecionar')
            for el in candidatos:
                if not el.is_displayed():
                    continue
                txt = el.text.strip().lower()
                if any(k in txt for k in kw):
                    _clique_resiliente(driver, el)
                    log(f'[TM] Clicou botão de compra: "{el.text.strip()[:40]}"', 'TM')
                    clicou_compra = True
                    break
            if clicou_compra:
                break
    except Exception as e:
        log(f'[TM] Erro ao clicar botão compra: {e}', 'TM')

    if not clicou_compra:
        log('[TM] Botão de compra não encontrado — entregando para conclusão manual.', 'TM')
        return _aguardar_conclusao_manual(driver, data_show, 'TM', '-', 0)

    # ── 2. Aguardar checkout carregar ───────────────────────────────────────
    time.sleep(2)
    _aguardar_overlay_sumir(driver)
    screenshot(driver, f'tm_checkout_{data_show.replace("/", "")}')

    # ── 3. Selecionar Cartão de Crédito ────────────────────────────────────
    with Cronometro('tm_sel_cartao'):
        ok_cartao = _selecionar_cartao_credito(driver)

    if not ok_cartao:
        log('[TM] Não selecionou cartão de crédito — entregando para conclusão manual.', 'TM')
        notificar(
            '⚠️ TM: Seleção de cartão falhou',
            f'{data_show}: não consegui selecionar cartão. Página ABERTA para você finalizar.',
            'urgent',
        )
        return _aguardar_conclusao_manual(driver, data_show, 'TM', '-', 0)

    log('[TM] Cartão de crédito selecionado.', 'TM')

    # ── 4. Selecionar 3x parcelas ───────────────────────────────────────────
    with Cronometro('tm_sel_parcelas'):
        ok_parcelas = _selecionar_parcelas(driver, n=3)

    if not ok_parcelas:
        log('[TM] Não selecionou 3x — continuando (pode não ter parcelas disponíveis).', 'TM')
        # Não aborta: algumas compras não oferecem parcelamento; segue em frente.

    # ── 5. Preencher dados pessoais ─────────────────────────────────────────
    try:
        buyticket_preencher_dados(driver)
    except Exception as e:
        log(f'[TM] Erro ao preencher dados: {e}. Página aberta para conclusão manual.', 'TM')
        return _aguardar_conclusao_manual(driver, data_show, 'TM', '-', 0)

    # ── 6. Clicar em Finalizar / Confirmar / Pagar ──────────────────────────
    clicou_finalizar = False
    fim_btn = time.monotonic() + 15
    textos_finalizar = ['finalizar', 'confirmar', 'pagar', 'comprar agora', 'place order',
                        'submit', 'checkout', 'concluir']
    while time.monotonic() < fim_btn and not clicou_finalizar:
        for b in driver.find_elements(By.TAG_NAME, 'button'):
            try:
                txt = (b.text or '').strip().lower()
                if any(t in txt for t in textos_finalizar):
                    if not b.get_attribute('disabled') and b.is_displayed():
                        driver.execute_script('arguments[0].click();', b)
                        log(f'[TM] Clicou "{b.text.strip()[:40]}" para finalizar.', 'TM')
                        clicou_finalizar = True
                        break
            except Exception:
                continue
        if not clicou_finalizar:
            time.sleep(0.3)

    if not clicou_finalizar:
        log('[TM] Botão de finalizar não encontrado — entregando para conclusão manual.', 'TM')
        return _aguardar_conclusao_manual(driver, data_show, 'TM', '-', 0)

    screenshot(driver, f'tm_finalizando_{data_show.replace("/", "")}')

    # ── 7. Aguardar confirmação do gateway ──────────────────────────────────
    return buyticket_aguardar_confirmacao_cartao(driver, data_show, 'TM', '-', 0)


def monitor_ticketmaster():
    """
    Thread de monitoramento do Ticketmaster.

    Abre 3 abas simultâneas (uma por dia de show) e faz polling cíclico.
    Notificação urgente somente quando o estado muda de não-disponivel →
    disponivel (evita spam).
    """
    log('Iniciando monitor Ticketmaster (3 abas)...', 'TM')
    notificar(
        'Monitor Ticketmaster',
        f'Monitor iniciado. Monitorando {len(URLS_TICKETMASTER)} datas: '
        f'{", ".join(DATAS_SHOW)}',
    )

    driver = None
    handles: list = []
    ultima_checagem_login_tm: float | None = None

    def _abrir_abas() -> list:
        """Abre uma aba por dia e retorna lista de window handles."""
        _handles = []
        driver.get(URLS_TICKETMASTER[0])
        _wait_for(driver, 'body', timeout=10)
        _handles.append(driver.current_window_handle)
        for url in URLS_TICKETMASTER[1:]:
            driver.execute_script(f'window.open("{url}", "_blank");')
            time.sleep(1.5)
            driver.switch_to.window(driver.window_handles[-1])
            _wait_for(driver, 'body', timeout=10)
            _handles.append(driver.current_window_handle)
        log(f'Abas TM abertas: {len(_handles)}', 'TM')
        return _handles

    while True:
        try:
            if driver is None:
                driver = criar_driver()
                ticketmaster_login(driver)
                ultima_checagem_login_tm = time.monotonic()
                handles = _abrir_abas()

            sessao_morta = False
            for idx, handle in enumerate(handles):
                data_show = DATAS_SHOW[idx]
                url = URLS_TICKETMASTER[idx]

                try:
                    driver.switch_to.window(handle)
                    driver.refresh()
                    _wait_for(driver, 'body', timeout=12)

                    if '/login' in (driver.current_url or '').lower():
                        log(f'[{data_show}] TM: redirect para /login detectado — sessao expirada', 'TM')
                        sessao_morta = True
                        break

                    novo_estado = _tm_classificar(driver, data_show)
                    estado_anterior = _ESTADO_TM.get(data_show)

                    # ── Atualiza estado e decide se notifica ──────────────
                    if novo_estado == 'esgotado':
                        if estado_anterior != 'esgotado':
                            log(f'[{data_show}] TM: ESGOTADO', 'TM')
                        _ESTADO_TM[data_show] = 'esgotado'

                    elif novo_estado == 'disponivel':
                        _ESTADO_TM[data_show] = 'disponivel'
                        if estado_anterior != 'disponivel':
                            # ⚡ Estado mudou → notifica urgente
                            precos_raw = re.findall(r'R\$\s*[\d.,]+', driver.page_source)
                            precos_str = ', '.join(dict.fromkeys(precos_raw[:6]))
                            log(f'[{data_show}] TM: ⚡ INGRESSOS DISPONÍVEIS!', 'TM')
                            notificar(
                                f'TM INGRESSOS {data_show}',
                                f'Ticketmaster {data_show} — botao de compra ATIVO!\n'
                                f'URL: {url}\n'
                                f'Precos: {precos_str or "—"}',
                                'urgent',
                            )
                            notificar_pc(
                                f'🎟️ TM Ingresso disponivel! {data_show}',
                                f'Ticketmaster — botao de compra ATIVO!\nPrecos: {precos_str or "—"}\n{url}',
                                'urgent',
                            )
                            screenshot(driver, f'tm_disponivel_{data_show.replace("/", "")}')

                            # ── Tentativa automática de compra ────────────────
                            # Executa na mesma aba. Se conseguir comprar, marca
                            # o estado como 'comprado' para não tentar de novo.
                            # Se falhar ou cair no manual, a página fica aberta.
                            try:
                                resultado_compra = ticketmaster_tentar_comprar(
                                    driver, data_show, url
                                )
                                if resultado_compra is True:
                                    _ESTADO_TM[data_show] = 'comprado'
                                    log(f'[{data_show}] TM: ✅ COMPRA CONCLUÍDA', 'TM')
                                else:
                                    log(
                                        f'[{data_show}] TM: compra não concluída '
                                        f'automaticamente (resultado={resultado_compra})',
                                        'TM',
                                    )
                            except Exception as e_compra:
                                log(f'[{data_show}] TM: erro na tentativa de compra: {e_compra}', 'TM')
                                screenshot(driver, f'tm_erro_compra_{data_show.replace("/", "")}')
                        else:
                            log(f'[{data_show}] TM: disponivel (aguardando pagamento)', 'TM')

                    else:  # desconhecido
                        if estado_anterior is None:
                            log(f'[{data_show}] TM: estado inicial desconhecido '
                                f'(sem botao ativo e sem "esgotado")', 'TM')
                        _ESTADO_TM.setdefault(data_show, 'desconhecido')

                except InvalidSessionIdException:
                    log(f'[{data_show}] TM: sessao morta, reiniciando driver...', 'TM')
                    sessao_morta = True
                    break
                except Exception as e:
                    if _e_erro_de_rede(e):
                        log(f'[{data_show}] TM: erro de rede na aba — aguardando... '
                            f'({str(e)[:80]})', 'TM')
                        _aguardar_rede()
                    else:
                        log(f'[{data_show}] TM: erro — {e}', 'TM')

                time.sleep(INTERVALO_CHECK)

            ultima_checagem_login_tm = _checar_login_periodico_tm(driver, ultima_checagem_login_tm)

            if sessao_morta:
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
                handles = []

        except Exception as e:
            if _e_erro_de_rede(e):
                # Erro transitório de rede (standby, troca de Wi-Fi, etc.).
                # NÃO mata o driver, NÃO envia notificação de alarme.
                # Aguarda a rede voltar e retoma silenciosamente.
                log(f'TM: erro de rede transitorio — aguardando conectividade... '
                    f'({str(e)[:100]})', 'TM')
                _aguardar_rede()
                time.sleep(3)
                # Handles continuam válidos — apenas recarrega no próximo ciclo.
            else:
                # Crash real do browser (driver morto, OOM, etc.).
                log(f'TM: erro fatal — {e}', 'TM')
                log(traceback.format_exc(), 'TM')
                notificar('Erro Ticketmaster', f'Browser crashou, reiniciando... {str(e)[:80]}')
                try:
                    if driver:
                        driver.quit()
                except Exception:
                    pass
                driver = None
                handles = []
                time.sleep(5)


# ============================================================
# ANTI-SUSPENSÃO (mantém o Windows acordado enquanto o bot roda)
# ============================================================

_ES_CONTINUOUS        = 0x80000000
_ES_SYSTEM_REQUIRED   = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040


def impedir_suspensao():
    """Impede o Windows de suspender enquanto o bot roda."""
    if os.name != 'nt':
        log('Anti-suspensao: ignorado (nao e Windows)', 'BOT')
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED
        )
        log('Anti-suspensao ATIVADO: Windows nao vai suspender enquanto o bot roda', 'BOT')
    except Exception as e:
        log(f'Anti-suspensao: falha ao ativar ({e}). O sistema pode suspender.', 'BOT')


def liberar_suspensao():
    """Reverte o bloqueio, permitindo o Windows suspender normalmente."""
    if os.name != 'nt':
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        log('Anti-suspensao DESATIVADO: Windows pode suspender normalmente', 'BOT')
    except Exception:
        pass


# ============================================================
# TRAVA DE INSTÂNCIA ÚNICA
# ============================================================

_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot.lock')


def _pid_esta_vivo(pid):
    if pid <= 0:
        return False
    try:
        if os.name == 'nt':
            saida = os.popen(f'tasklist /FI "PID eq {pid}" /NH').read()
            return str(pid) in saida
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def adquirir_lock_instancia():
    try:
        if os.path.exists(_LOCK_PATH):
            try:
                with open(_LOCK_PATH, 'r', encoding='utf-8') as f:
                    pid_antigo = int((f.read() or '0').strip() or '0')
            except Exception:
                pid_antigo = 0
            if pid_antigo and _pid_esta_vivo(pid_antigo):
                log(f'JA EXISTE um bot rodando (PID {pid_antigo}). Abortando esta '
                    f'instancia para evitar 2 bots ao mesmo tempo.', 'BOT')
                notificar('Bot NAO iniciado',
                          f'Ja existe uma instancia rodando (PID {pid_antigo}). '
                          f'Feche a outra antes de abrir uma nova.')
                return False
            log(f'Lock orfao encontrado (PID {pid_antigo} morto). Reaproveitando.', 'BOT')

        with open(_LOCK_PATH, 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log(f'Aviso: falha ao criar lock de instancia ({e}). Prosseguindo.', 'BOT')
        return True


def liberar_lock_instancia():
    try:
        if os.path.exists(_LOCK_PATH):
            with open(_LOCK_PATH, 'r', encoding='utf-8') as f:
                pid = int((f.read() or '0').strip() or '0')
            if pid == os.getpid():
                os.remove(_LOCK_PATH)
    except Exception:
        pass


def limpar_chromes_orfaos():
    """Encerra processos Chrome/chromedriver órfãos de automação."""
    if os.name != 'nt':
        return
    marcadores = ('enable-automation', 'test-type', 'headless', 'remote-debugging')
    mortos = 0
    try:
        import subprocess
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \""
            "name='chrome.exe' OR name='chromedriver.exe'\" | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
        )
        saida = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=20,
        ).stdout
        for linha in saida.splitlines():
            if '|' not in linha:
                continue
            pid_str, _, cmdline = linha.partition('|')
            cmd_low = (cmdline or '').lower()
            if any(m in cmd_low for m in marcadores):
                try:
                    pid = int(pid_str.strip())
                    os.kill(pid, 9)
                    mortos += 1
                except Exception:
                    pass
    except Exception as e:
        log(f'Limpeza de Chromes orfaos: aviso ({e}). Prosseguindo.', 'BOT')
        return
    if mortos:
        log(f'Limpeza inicial: {mortos} processo(s) Chrome de automacao orfao(s) encerrado(s)', 'BOT')
    else:
        log('Limpeza inicial: nenhum Chrome de automacao orfao encontrado', 'BOT')


# ============================================================
# MAIN
# ============================================================


def main():
    global CHROMEDRIVER_PATH

    print('=' * 60)
    print('  BOT DE INGRESSOS BTS 2026 — BuyTicket + Ticketmaster')
    print(f'  Datas: {", ".join(DATAS_SHOW)}')
    sites_ativos = []
    if ATIVAR_BUYTICKET:
        sites_ativos.append('BuyTicket')
    if ATIVAR_TICKETMASTER:
        sites_ativos.append('Ticketmaster')
    print(f'  Sites: {" + ".join(sites_ativos) if sites_ativos else "nenhum"}')
    print('=' * 60)
    print()

    if ATIVAR_BUYTICKET and BUYTICKET_MODO_MONITOR:
        print('  [BuyTicket] Modo Monitor — sem credenciais configuradas.')
        print('  → Monitorando preços e notificando, mas NÃO vai comprar automaticamente.')
        print('  → Para compra automática: preencha BUYTICKET_CPF e BUYTICKET_SENHA no .env')
        print()

    if not ATIVAR_BUYTICKET and not ATIVAR_TICKETMASTER:
        print('ERRO: MONITOR_ONLY inválido. Use "ambos", "buyticket" ou "ticketmaster".')
        return

    if not NTFY_TOPIC:
        print('ERRO: NTFY_TOPIC deve estar no .env')
        return

    if not adquirir_lock_instancia():
        print('\nERRO: Ja existe uma instancia do bot rodando. Encerrando esta.')
        return

    limpar_chromes_orfaos()
    impedir_suspensao()

    log('Baixando/verificando ChromeDriver...')
    CHROMEDRIVER_PATH = ChromeDriverManager().install()
    log(f'ChromeDriver OK: {CHROMEDRIVER_PATH}')

    notificar(
        'Bot Iniciado',
        f'Monitorando: {" + ".join(sites_ativos)}\n'
        f'Intervalo: {INTERVALO_CHECK}s\n'
        f'Maior limite: R${MAIOR_LIMITE}\n'
        f'CDP bloqueio: {"ativo" if CDP_BLOQUEIO else "desativado"}',
    )

    print('\nLimites de preco configurados:')
    for setor, limites in LIMITES_PRECO.items():
        print(f'  {setor}: inteira=R${limites["inteira"]}, meia=R${limites["meia"]}')
    print()

    threads = []
    if ATIVAR_BUYTICKET:
        thread_bt = threading.Thread(target=monitor_buyticket, daemon=True, name='BuyTicket')
        threads.append(thread_bt)
        thread_bt.start()
        log('Thread BuyTicket iniciada', 'BOT')

    if ATIVAR_TICKETMASTER:
        thread_tm = threading.Thread(target=monitor_ticketmaster, daemon=True, name='Ticketmaster')
        threads.append(thread_tm)
        thread_tm.start()
        log('Thread Ticketmaster iniciada', 'BOT')

    log(f'Threads iniciadas ({len(threads)}). Monitorando... (Ctrl+C para parar)')

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n\nEncerrando bot...')
        notificar('Bot Encerrado', 'Bot de ingressos foi encerrado manualmente.')
        print('Bot encerrado.')
    finally:
        liberar_suspensao()
        liberar_lock_instancia()


if __name__ == '__main__':
    main()


