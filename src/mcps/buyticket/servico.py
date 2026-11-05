"""
Camada de serviço reutilizável para a automação do BuyTicket.

Este módulo NÃO reimplementa a automação: ele orquestra as funções já
existentes em `bot.py` (Selenium) e a lógica pura de `ticket_logic.py`,
mantendo o estado do navegador (driver + abas por dia) dentro de uma única
instância de `SessaoBuyTicket`.

É consumido pelo servidor MCP (`servidor.py`), mas também pode ser usado
diretamente em scripts/testes.

Política de segurança (decisão do projeto):
  - O fluxo executa TUDO até gerar o código PIX.
  - A finalização/compra real fica atrás de uma trava explícita
    (`permitir_pagamento`), DESLIGADA por padrão. Enquanto desligada, o
    `bot.DRY_RUN` é forçado para True, de modo que `buyticket_efetuar_compra`
    nunca clica no botão de compra.

Requisitos de ambiente:
  - As dependências da automação (selenium, webdriver-manager, python-dotenv)
    precisam estar instaladas — ver requirements.txt.
  - O `.env` do projeto deve conter credenciais e dados pessoais (ver .env.example).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Garante que a raiz do projeto (onde estão bot.py e ticket_logic.py) esteja
# no sys.path, independentemente de onde o servidor MCP for iniciado.
_RAIZ_PROJETO = Path(__file__).resolve().parents[3]
if str(_RAIZ_PROJETO) not in sys.path:
    sys.path.insert(0, str(_RAIZ_PROJETO))

import bot  # noqa: E402  (import após ajuste de sys.path)
import ticket_logic  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Seletores da tela de fila CrowdHandler (confirmados em capturas/10-fila.html)
# ─────────────────────────────────────────────────────────────────────────────
#
# O BuyTicket usa o serviço CrowdHandler (wait.crowdhandler.com) como sala de
# espera virtual. A tela é injetada na mesma aba do evento e pode aparecer a
# qualquer momento após clicar em COMPRAR.
#
# Estados possíveis:
#   EM_FILA      – section#ch-active com display:block  → usuário aguarda
#   BLOQUEADO    – section#ch-blocked com display:block → acesso negado
#   FILA_CHEIA   – section#ch-room-full com display:block
#   FILA_FECHADA – section#ch-prelude com display:block → ainda não abriu
#   LIBERADO     – section#ch-active com display:none  → passou para o site
#
# Detecção de SAÍDA da fila: #ch-active passa a display:none  OU  a URL muda
# para fora de wait.crowdhandler.com. Ambas as condições são monitoradas.
#
SELETORES_FILA = {
    # Seção visível enquanto o usuário está na fila
    'ativo':       'section#ch-active',
    # Número da posição ("368")
    'posicao':     'strong#ch-count',
    # Texto com minutos estimados ("7")
    'minutos':     '#ch-estimated-minutes .ch-number',
    # Texto com horas estimadas
    'horas':       '#ch-estimated-hours .ch-number',
    # Barra de progresso
    'progresso':   '#ch-progress',
    # Acesso bloqueado
    'bloqueado':   'section#ch-blocked',
    # Fila ainda não abriu
    'prelude':     'section#ch-prelude',
    # Fila cheia
    'fila_cheia':  'section#ch-room-full',
    # Logo do CrowdHandler (confirma que ESTAMOS na página de fila)
    'logo_ch':     'section#ch-logo',
    # ID do token de sessão da fila
    'token':       '#ch-token',
}

# Domínio injetado pelo CrowdHandler na URL durante a espera
_DOMINIO_FILA = 'wait.crowdhandler.com'

# Textos alternativos que confirmam presença na fila (fallback para page_source)
_TEXTOS_FILA = (
    'todo mundo quer nossos ingressos',
    'posição na fila',
    'posicao na fila',
    'crowdhandler',
    'ch-active',
    'sua posição na fila',
)

# Intervalo de polling rápido em segundos (100 ms)
_POLL_INTERVALO_S = 0.10

# Intervalo de polling de log de progresso (a cada N polls mostra o log)
_LOG_A_CADA_N_POLLS = 30   # ≈ 3 s


def _detectar_fila_rapido(driver) -> bool:
    """
    Detecção rápida de fila de espera — não usa time.sleep.

    Critérios (qualquer um basta):
      1. URL contém o domínio do CrowdHandler.
      2. section#ch-active está visível no DOM.
      3. Fallback textual (page_source) para os textos-chave da fila.

    Retorna True se o bot está na fila, False caso contrário.
    """
    try:
        url = (driver.current_url or '').lower()
        if _DOMINIO_FILA in url:
            return True
        # Verificação DOM — mais precisa e sem carregar o page_source inteiro.
        els = driver.find_elements('css selector', SELETORES_FILA['ativo'])
        if els and els[0].is_displayed():
            return True
        # Fallback leve: page_source (mais caro, mas captura variações de layout).
        src = driver.page_source.lower()
        return any(t in src for t in _TEXTOS_FILA)
    except Exception:
        return False


def _ler_posicao_fila(driver) -> dict[str, Any]:
    """
    Lê a posição atual na fila e o tempo estimado de espera.
    Retorna dict com 'posicao' (int|None), 'minutos' (int|None), 'horas' (int|None).
    """
    dados: dict[str, Any] = {'posicao': None, 'minutos': None, 'horas': None}
    try:
        els_pos = driver.find_elements('css selector', SELETORES_FILA['posicao'])
        if els_pos:
            txt = (els_pos[0].text or '').strip().replace('.', '').replace(',', '')
            if txt.isdigit():
                dados['posicao'] = int(txt)

        els_min = driver.find_elements('css selector', SELETORES_FILA['minutos'])
        if els_min:
            t = (els_min[0].text or '').strip()
            if t.isdigit():
                dados['minutos'] = int(t)

        els_hr = driver.find_elements('css selector', SELETORES_FILA['horas'])
        if els_hr:
            t = (els_hr[0].text or '').strip()
            if t.isdigit():
                dados['horas'] = int(t)
    except Exception:
        pass
    return dados


class ErroSessao(RuntimeError):
    """Erro de operação de sessão (ex.: ação que exige navegador sem sessão ativa)."""


class SessaoBuyTicket:
    """
    Encapsula uma sessão de automação do BuyTicket (um navegador + abas por dia).

    Uso típico (via servidor MCP):
        s = SessaoBuyTicket()
        s.iniciar()                       # cria driver + login + abas por dia
        s.consultar_ofertas('28/10')      # leitura, sem comprar
        s.preparar_compra('28/10')        # vai até o checkout (modo seguro)
        s.obter_codigo_pix()              # lê o código PIX para pagar manualmente
        s.encerrar()
    """

    def __init__(self) -> None:
        self._driver = None
        self._abas: dict[str, dict] = {}
        # Lock reentrante: as operações são serializadas (um navegador só).
        self._lock = threading.RLock()
        # Trava de pagamento. Enquanto False, força bot.DRY_RUN=True.
        self._permitir_pagamento = False
        self._chromedriver_path: Optional[str] = None

    # ----------------------------------------------------------------- estado

    @property
    def ativa(self) -> bool:
        """True se há um driver instanciado E o Chrome ainda responde."""
        if self._driver is None:
            return False
        try:
            # Operação leve: só acessa uma propriedade do driver.
            # Lança exceção se o Chrome foi fechado/crashou.
            _ = self._driver.current_url
            return True
        except Exception:
            # Chrome morto — limpa o estado para forçar reinício.
            self._driver = None
            self._abas = {}
            return False

    def status(self) -> dict[str, Any]:
        """
        Retorna um retrato do estado atual da sessão (sem exigir navegador).

        Campos:
          - ativa: se há driver aberto
          - logado: se a sessão do BuyTicket está ativa (só checa se houver driver)
          - dias_abertos: datas com aba aberta
          - permitir_pagamento: estado da trava de pagamento
          - dry_run: valor efetivo de bot.DRY_RUN
        """
        with self._lock:
            logado = None
            if self._driver is not None:
                try:
                    logado = bool(bot.buyticket_sessao_ativa(self._driver))
                except Exception:
                    logado = None
            return {
                'ativa': self.ativa,
                'logado': logado,
                'dias_abertos': sorted(self._abas.keys()),
                'permitir_pagamento': self._permitir_pagamento,
                'dry_run': bot.DRY_RUN,
            }

    # ------------------------------------------------------------- ciclo vida

    def iniciar(self, headless: Optional[bool] = None) -> dict[str, Any]:
        """
        Cria o navegador, faz login no BuyTicket e abre uma aba por dia de show.

        - headless: se informado, sobrepõe bot.HEADLESS para esta sessão.
        - Baixa/garante o ChromeDriver uma única vez (bot.CHROMEDRIVER_PATH).
        - É idempotente: se já houver sessão ativa, apenas retorna o status.
        """
        with self._lock:
            if self._driver is not None:
                return {'ja_ativa': True, **self.status()}

            if headless is not None:
                bot.HEADLESS = bool(headless)

            # Garante o ChromeDriver (bot.criar_driver usa bot.CHROMEDRIVER_PATH).
            if self._chromedriver_path is None:
                from webdriver_manager.chrome import ChromeDriverManager
                self._chromedriver_path = ChromeDriverManager().install()
            bot.CHROMEDRIVER_PATH = self._chromedriver_path

            self._driver = bot.criar_driver()
            bot.buyticket_login(self._driver)
            self._abas = bot.abrir_abas_por_dia(
                self._driver, bot.URLS_BUYTICKET, bot.DATAS_SHOW
            )
            return {'ja_ativa': False, **self.status()}

    def encerrar(self) -> dict[str, Any]:
        """Fecha o navegador e limpa o estado. Seguro chamar mesmo sem sessão."""
        with self._lock:
            if self._driver is not None:
                try:
                    self._driver.quit()
                except Exception:
                    pass
            self._driver = None
            self._abas = {}
            return {'encerrada': True}

    # ------------------------------------------------------------ leitura/uso

    def _garantir_sessao(self) -> None:
        """
        Garante que há um driver vivo. Se o Chrome crashou, reinicia
        automaticamente (login + abas por dia) sem precisar chamar iniciar().
        """
        if not self.ativa:
            bot.log('Chrome morto — reiniciando sessão automaticamente...', 'MCP')
            if self._chromedriver_path is None:
                from webdriver_manager.chrome import ChromeDriverManager
                self._chromedriver_path = ChromeDriverManager().install()
            bot.CHROMEDRIVER_PATH = self._chromedriver_path
            self._driver = bot.criar_driver()
            bot.buyticket_login(self._driver)
            self._abas = bot.abrir_abas_por_dia(
                self._driver, bot.URLS_BUYTICKET, bot.DATAS_SHOW
            )
            bot.log('Sessão reiniciada com sucesso.', 'MCP')

    def _exige_driver(self):
        if self._driver is None:
            raise ErroSessao('Nenhuma sessão ativa. Chame iniciar() primeiro.')
        return self._driver

    def _validar_dia(self, dia: str) -> dict:
        aba = self._abas.get(dia)
        if not aba:
            raise ErroSessao(
                f"Dia '{dia}' não tem aba aberta. Dias disponíveis: "
                f"{sorted(self._abas.keys())}"
            )
        return aba

    def consultar_ofertas(self, dia: str) -> dict[str, Any]:
        """
        Lê, para o dia informado, o preço do botão e os setores/categorias
        disponíveis. NÃO compra e NÃO seleciona nada de forma definitiva.
        """
        with self._lock:
            self._garantir_sessao()
            driver = self._exige_driver()
            aba = self._validar_dia(dia)
            bot.buyticket_refresh_dia(driver, aba)

            preco_botao = bot.buyticket_ler_preco_botao(driver)
            setores = bot.buyticket_ler_setores_disponiveis(driver)
            categorias = bot.buyticket_ler_categorias_disponiveis(driver)
            return {
                'dia': dia,
                'preco_botao': preco_botao,
                'setores': setores,
                'categorias': categorias,
            }

    def status_fila(self, dia: str) -> dict[str, Any]:
        """
        Lê o estado instantâneo da fila de espera para o dia informado,
        sem aguardar nem bloquear.

        Retorna:
            {
              'em_fila': bool,
              'posicao': int | None,       # número de pessoas na frente
              'minutos': int | None,       # tempo estimado em minutos
              'horas':   int | None,       # tempo estimado em horas (se mostrado)
              'bloqueado': bool,           # acesso negado pelo CrowdHandler
              'dia': str,
            }
        """
        with self._lock:
            driver = self._exige_driver()
            aba = self._validar_dia(dia)
            driver.switch_to.window(aba['handle'])

            em_fila = _detectar_fila_rapido(driver)
            pos = _ler_posicao_fila(driver) if em_fila else {'posicao': None, 'minutos': None, 'horas': None}

            bloqueado = False
            if em_fila:
                try:
                    els = driver.find_elements('css selector', SELETORES_FILA['bloqueado'])
                    bloqueado = bool(els and els[0].is_displayed())
                except Exception:
                    pass

            return {
                'em_fila': em_fila,
                'posicao': pos['posicao'],
                'minutos': pos['minutos'],
                'horas': pos['horas'],
                'bloqueado': bloqueado,
                'dia': dia,
            }

    def aguardar_fila(self, dia: str, timeout_min: int = 20) -> dict[str, Any]:
        """
        Monitora ativamente a fila CrowdHandler e retorna assim que o acesso
        for liberado — ou no timeout.

        Estratégia de alta performance:
          • Polling a cada 100 ms verificando DOM (#ch-active) + URL.
          • A cada ~3 s loga posição e tempo estimado.
          • Retorna IMEDIATAMENTE quando a liberação real ocorrer, sem esperar
            contadores visuais: o contador pode ainda mostrar "1" quando a tela
            já foi substituída pelo site do evento (comportamento documentado
            em testes manuais com até 700 pessoas na frente).

        Argumentos:
            dia         – ex.: '28/10', '30/10', '31/10'
            timeout_min – tempo máximo de espera em minutos (padrão: 20)

        Retorna:
            {
              'liberado': bool,     # True = saiu da fila; False = timeout/bloqueio
              'motivo': str,        # 'liberado' | 'timeout' | 'bloqueado' | 'sem_fila'
              'posicao_inicial': int | None,
              'duracao_s': float,   # segundos que ficou em fila
              'dia': str,
            }
        """
        with self._lock:
            driver = self._exige_driver()
            aba = self._validar_dia(dia)
            driver.switch_to.window(aba['handle'])

            # Se não há fila neste momento, retorna imediatamente.
            if not _detectar_fila_rapido(driver):
                return {
                    'liberado': True,
                    'motivo': 'sem_fila',
                    'posicao_inicial': None,
                    'duracao_s': 0.0,
                    'dia': dia,
                }

            pos_inicial = _ler_posicao_fila(driver)
            bot.log(
                f'[{dia}] Fila detectada. Posição inicial: {pos_inicial["posicao"]} '
                f'| Espera estimada: {pos_inicial["minutos"]} min', 'FILA'
            )
            bot.notificar(
                'Fila de Espera',
                f'{dia}: posição {pos_inicial["posicao"]}, '
                f'~{pos_inicial["minutos"]} min de espera. Monitorando...',
            )

            t_inicio = time.monotonic()
            t_limite = t_inicio + timeout_min * 60
            n_poll = 0

        # ─── Loop de polling fora do lock para não bloquear outras chamadas ───
        # O driver só é acessado de uma thread por vez (o lock é reentrado
        # somente quando necessário). Aqui o loop é síncrono mas com sleep
        # mínimo de 100 ms, o que garante reação em < 200 ms após a liberação.
        while time.monotonic() < t_limite:
            time.sleep(_POLL_INTERVALO_S)
            n_poll += 1

            try:
                # ── Verificação primária: DOM + URL ──────────────────────────
                ainda_em_fila = _detectar_fila_rapido(driver)

                if not ainda_em_fila:
                    duracao = time.monotonic() - t_inicio
                    bot.log(
                        f'[{dia}] FILA LIBERADA em {duracao:.1f}s '
                        f'(posição inicial: {pos_inicial["posicao"]})', 'FILA'
                    )
                    bot.notificar(
                        'Fila Liberada!',
                        f'{dia}: saiu da fila em {duracao:.0f}s. '
                        f'Executando seleção de ingresso...',
                        'high',
                    )
                    return {
                        'liberado': True,
                        'motivo': 'liberado',
                        'posicao_inicial': pos_inicial['posicao'],
                        'duracao_s': round(duracao, 2),
                        'dia': dia,
                    }

                # ── Verificação de bloqueio ──────────────────────────────────
                try:
                    els_bloq = driver.find_elements('css selector', SELETORES_FILA['bloqueado'])
                    if els_bloq and els_bloq[0].is_displayed():
                        duracao = time.monotonic() - t_inicio
                        bot.log(f'[{dia}] Acesso BLOQUEADO pelo CrowdHandler.', 'FILA')
                        bot.notificar(
                            'Acesso Bloqueado',
                            f'{dia}: CrowdHandler bloqueou o acesso por atividade suspeita.',
                            'urgent',
                        )
                        return {
                            'liberado': False,
                            'motivo': 'bloqueado',
                            'posicao_inicial': pos_inicial['posicao'],
                            'duracao_s': round(duracao, 2),
                            'dia': dia,
                        }
                except Exception:
                    pass

                # ── Log periódico de progresso ───────────────────────────────
                if n_poll % _LOG_A_CADA_N_POLLS == 0:
                    pos_atual = _ler_posicao_fila(driver)
                    restante_min = int((t_limite - time.monotonic()) / 60)
                    bot.log(
                        f'[{dia}] Na fila: posição {pos_atual["posicao"]} '
                        f'| ~{pos_atual["minutos"]} min | timeout em {restante_min} min',
                        'FILA',
                    )

            except Exception as e:
                bot.log(f'[{dia}] Erro no polling de fila: {e}', 'FILA')

        # ── Timeout ──────────────────────────────────────────────────────────
        duracao = time.monotonic() - t_inicio
        bot.log(f'[{dia}] Timeout aguardando sair da fila ({timeout_min} min).', 'FILA')
        bot.notificar(
            'Timeout Fila',
            f'{dia}: não saiu da fila em {timeout_min} min. Abortando tentativa.',
        )
        return {
            'liberado': False,
            'motivo': 'timeout',
            'posicao_inicial': pos_inicial['posicao'],
            'duracao_s': round(duracao, 2),
            'dia': dia,
        }

    def preparar_compra(self, dia: str) -> dict[str, Any]:
        """
        Executa o fluxo de compra até o ponto seguro (ou real com trava ligada).
        """
        with self._lock:
            self._garantir_sessao()
            driver = self._exige_driver()
            aba = self._validar_dia(dia)
            bot.buyticket_refresh_dia(driver, aba)

            # ── Verificação de fila antes de qualquer seleção ────────────────
            if _detectar_fila_rapido(driver):
                resultado_fila = self.aguardar_fila(dia)
                if not resultado_fila['liberado']:
                    return {
                        'dia': dia,
                        'fluxo_sucesso': False,
                        'motivo': f'abortado_por_fila: {resultado_fila["motivo"]}',
                        'fila': resultado_fila,
                        **self.status(),
                    }
                # Após sair da fila, recarrega a aba para estado limpo.
                bot.buyticket_refresh_dia(driver, aba)

            # Espelha a trava no DRY_RUN do bot (fonte de verdade do clique final).
            bot.DRY_RUN = not self._permitir_pagamento
            bot.screenshot(driver, f'pre_fluxo_{dia.replace("/", "")}')
            sucesso = bot.buyticket_fluxo_compra(driver, dia, aba['url'])
            return {
                'dia': dia,
                'fluxo_sucesso': bool(sucesso),
                'modo_seguro': not self._permitir_pagamento,
                **self.status(),
            }

    def obter_codigo_pix(self, dia: str = '', setor: str = '',
                         categoria: str = '', preco: int = 0) -> dict[str, Any]:
        """
        Lê o código PIX na tela atual (após preparar_compra com pagamento
        liberado). Reaproveita bot.buyticket_aguardar_pix, que procura o
        payload PIX (começa com '0002') no DOM/inputs.

        Só faz sentido com a trava de pagamento LIGADA e após o checkout.
        Retorna {'encontrou': bool}. O código em si é logado/notificado pelo bot.
        """
        with self._lock:
            driver = self._exige_driver()
            if not self._permitir_pagamento:
                raise ErroSessao(
                    'Trava de pagamento desligada: o fluxo não chega ao PIX em modo '
                    'seguro. Ligue com permitir_pagamento(True) antes.'
                )
            encontrou = bot.buyticket_aguardar_pix(driver, dia, setor, categoria, preco)
            return {'encontrou': bool(encontrou)}

    # -------------------------------------------------------------- trava/pgto

    def permitir_pagamento(self, permitir: bool) -> dict[str, Any]:
        """
        Liga/desliga a trava que autoriza o fluxo a chegar até o pagamento/PIX.

        Espelha imediatamente em bot.DRY_RUN (permitir=True -> DRY_RUN=False).
        Padrão do sistema: DESLIGADA (modo seguro).
        """
        with self._lock:
            self._permitir_pagamento = bool(permitir)
            bot.DRY_RUN = not self._permitir_pagamento
            return {
                'permitir_pagamento': self._permitir_pagamento,
                'dry_run': bot.DRY_RUN,
            }


# ---------------------------------------------------------------- utilidades


def mapa_limites() -> dict[str, Any]:
    """Resumo dos limites e prioridades (lógica pura, sem navegador)."""
    return {
        'limites_preco': ticket_logic.LIMITES_PRECO,
        'maior_limite': ticket_logic.MAIOR_LIMITE,
        'mapa_setor': ticket_logic.MAPA_SETOR,
        'mapa_categoria': ticket_logic.MAPA_CATEGORIA,
        'datas_show': bot.DATAS_SHOW,
    }


def analisar_preco(setor: str, tipo: str, preco: float) -> dict[str, Any]:
    """
    Diz se (setor, tipo, preco) está dentro do limite configurado.

    Usa o mapeamento de setor de ticket_logic para normalizar o nome e compara
    com LIMITES_PRECO. Retorna aprovação, limite aplicável e a chave do setor.
    """
    setor_key = ticket_logic.mapear_setor(setor)
    tipo_norm = (tipo or '').strip().lower()
    if tipo_norm not in ('inteira', 'meia'):
        # Heurística simples: qualquer coisa com "meia" vira meia; senão inteira.
        tipo_norm = 'meia' if 'meia' in tipo_norm else 'inteira'

    limite = None
    aprovado = False
    if setor_key and setor_key in ticket_logic.LIMITES_PRECO:
        limite = ticket_logic.LIMITES_PRECO[setor_key].get(tipo_norm)
        if limite is not None:
            aprovado = float(preco) <= float(limite)

    return {
        'setor_informado': setor,
        'setor_key': setor_key,
        'tipo': tipo_norm,
        'preco': preco,
        'limite': limite,
        'aprovado': aprovado,
        'motivo': (
            'setor não reconhecido' if not setor_key else
            'sem limite para o tipo' if limite is None else
            'dentro do limite' if aprovado else
            f'preço {preco} acima do limite {limite}'
        ),
    }
