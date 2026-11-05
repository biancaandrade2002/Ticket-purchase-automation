"""
Servidor MCP (Model Context Protocol) para a automação BuyTicket.

Expõe a automação como ferramentas consumíveis por um assistente de IA,
cobrindo LEITURA/CONSULTA e EXECUÇÃO. Reaproveita a lógica de `bot.py`
(Selenium) e `ticket_logic.py` através da camada `servico.SessaoBuyTicket`.

Transporte: stdio (mesmo padrão do outro MCP do ambiente).
SDK: `mcp` (FastMCP).

============================ POLÍTICA DE SEGURANÇA ============================
O pagamento por PIX é IRREVERSÍVEL. Por isso:
  - Por padrão, a sessão opera em MODO SEGURO: o fluxo seleciona a oferta e
    valida preços, mas NÃO clica no botão de compra (bot.DRY_RUN=True).
  - Para chegar ao checkout/PIX, é preciso destravar explicitamente com a
    ferramenta `liberar_pagamento(confirmar=True)`.
  - A ferramenta `finalizar_pagamento` é a única que autoriza o fluxo real e
    exige `confirmar=True`; sem isso, ela recusa e não faz nada.
==============================================================================

Execução direta (para debug):
    py src/mcps/buyticket/servidor.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Garante a raiz do projeto no path (para importar servico/bot/ticket_logic).
_RAIZ_PROJETO = Path(__file__).resolve().parents[3]
if str(_RAIZ_PROJETO) not in sys.path:
    sys.path.insert(0, str(_RAIZ_PROJETO))

# Import relativo-tolerante: funciona rodando como script ou como módulo.
try:
    from servico import SessaoBuyTicket, analisar_preco as _analisar_preco, mapa_limites, SELETORES_FILA
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from servico import SessaoBuyTicket, analisar_preco as _analisar_preco, mapa_limites, SELETORES_FILA

from mcp.server.fastmcp import FastMCP

# Instância única de servidor e de sessão (um navegador por processo).
mcp = FastMCP('buyticket')
_sessao = SessaoBuyTicket()


# ============================================================
# FERRAMENTAS DE LEITURA / CONSULTA (não exigem confirmação)
# ============================================================


@mcp.tool()
def mapa_site() -> dict[str, Any]:
    """
    Resumo do mapeamento do site BuyTicket (fonte: docs/MCP-buyticket.md).

    Retorna os pontos-chave do fluxo e os seletores confirmados mais úteis,
    para orientar quem for operar/depurar a automação.
    """
    return {
        'url_base': os.getenv('BUYTICKET_URL_BASE', 'https://buyticketbrasil.com'),
        'login_url': os.getenv('BUYTICKET_LOGIN_URL', 'https://buyticketbrasil.com/entrar'),
        'modelo': 'marketplace de revenda (preços de terceiros, voláteis)',
        'tecnologia': 'SPA (IDs dinâmicos; usar seletores por texto/placeholder)',
        'fluxo': [
            'login (/entrar) — botão é div "Entrar na conta", não <button>',
            'evento — filtros: Tipo de ingresso + Categoria',
            'detalhe — texto "Setor | Tipo" + botão "Comprar agora por R$..."',
            'checkout (wizard) — dados por placeholder; PIX é radio value=PIX',
            'PIX — input[disabled] + botão #checkout_pix "Copiar código"',
        ],
        'seletores_chave': {
            'login_email': 'input[type="email"][placeholder="Seu email"]',
            'login_senha': 'input[type="password"][placeholder="Sua senha"]',
            'preco_botao': 'a[class*="buyBtn"]',
            'dropdown_head': 'button[class*="dropdownHead"]',
            'dropdown_item': 'button[class*="dropdownItem"]',
            'pix_radio': 'input[type="radio"][value="PIX"]',
            'pix_copiar': '#checkout_pix',
        },
        'seletores_fila': SELETORES_FILA,
        'observacao': 'Ver docs/MCP-buyticket.md para o mapa completo e o catálogo de estados.',
    }


@mcp.tool()
def listar_limites() -> dict[str, Any]:
    """
    Limites de preço por setor/tipo, maior limite, mapeamentos de nome e as
    datas dos shows. Lógica pura (não abre navegador).
    """
    return mapa_limites()


@mcp.tool()
def analisar_preco(setor: str, tipo: str, preco: float) -> dict[str, Any]:
    """
    Avalia se um ingresso (setor, tipo, preço) está dentro do limite configurado.

    - setor: nome livre (ex.: "Pista", "Arquibancada"); é normalizado.
    - tipo: "inteira" ou "meia" (aceita variações).
    - preco: valor numérico em reais.
    """
    return _analisar_preco(setor, tipo, preco)


@mcp.tool()
def status_sessao() -> dict[str, Any]:
    """
    Estado atual da sessão de automação: se há navegador aberto, se está logado,
    dias com aba aberta e o estado da trava de pagamento. Não abre navegador.
    """
    return _sessao.status()


@mcp.tool()
def consultar_ofertas(dia: str) -> dict[str, Any]:
    """
    Lê as ofertas do dia informado (preço do botão + setores + categorias)
    SEM comprar. Exige sessão iniciada (use iniciar_sessao antes).

    - dia: uma das datas dos shows, ex.: "28/10", "30/10", "31/10".
    """
    return _sessao.consultar_ofertas(dia)


@mcp.tool()
def check_queue_status(dia: str) -> dict[str, Any]:
    """
    Lê o estado instantâneo da fila de espera CrowdHandler para o dia informado.
    Não bloqueia nem aguarda: retorna imediatamente o snapshot atual.

    Retorna:
        em_fila   – True se a tela de fila está ativa agora
        posicao   – número de pessoas na frente (int ou null)
        minutos   – estimativa de espera em minutos (int ou null)
        horas     – estimativa de espera em horas (int ou null)
        bloqueado – True se o CrowdHandler bloqueou o acesso
        dia       – a data consultada

    Seletores confirmados (capturas/10-fila.html):
        #ch-active        → seção principal da fila (visível = em fila)
        #ch-count         → posição numérica
        #ch-estimated-minutes .ch-number → minutos estimados
        #ch-blocked       → acesso bloqueado

    Exige sessão iniciada (use iniciar_sessao antes).
    - dia: "28/10", "30/10" ou "31/10".
    """
    return _sessao.status_fila(dia)


@mcp.tool()
def wait_in_queue(dia: str, timeout_min: int = 20) -> dict[str, Any]:
    """
    Monitora ativamente a fila CrowdHandler e retorna assim que o acesso
    for liberado — sem depender de contadores visuais.

    Estratégia de alta performance (polling a cada 100 ms):
      • Verifica DOM (#ch-active) + URL a cada 100 ms.
      • Retorna IMEDIATAMENTE quando a tela de fila desaparece, sem esperar
        o contador chegar a 1 (bypass confirmado em testes manuais com 700+
        pessoas na frente: a liberação real precede o contador visual).
      • A cada ~3 s loga posição e tempo estimado para acompanhamento.
      • Detecta bloqueio (section#ch-blocked) e aborta com motivo='bloqueado'.

    Argumentos:
        dia         – "28/10", "30/10" ou "31/10"
        timeout_min – tempo máximo de espera em minutos (padrão: 20)

    Retorna:
        liberado        – True = saiu da fila; False = timeout ou bloqueio
        motivo          – 'liberado' | 'timeout' | 'bloqueado' | 'sem_fila'
        posicao_inicial – posição registrada ao entrar na fila
        duracao_s       – segundos totais em fila
        dia             – a data monitorada

    Exige sessão iniciada (use iniciar_sessao antes).
    """
    return _sessao.aguardar_fila(dia, timeout_min=timeout_min)


# ============================================================
# FERRAMENTAS DE EXECUÇÃO
# ============================================================


@mcp.tool()
def iniciar_sessao(headless: bool = False) -> dict[str, Any]:
    """
    Abre o navegador, faz login no BuyTicket e abre uma aba por dia de show.

    - headless: se True, roda sem janela visível.
    Idempotente: se já houver sessão ativa, apenas retorna o status.
    Requer credenciais no .env (BUYTICKET_CPF / BUYTICKET_SENHA).
    """
    return _sessao.iniciar(headless=headless)


@mcp.tool()
def encerrar_sessao() -> dict[str, Any]:
    """Fecha o navegador e limpa o estado da sessão. Seguro chamar sempre."""
    return _sessao.encerrar()


@mcp.tool()
def buscar_e_preparar_compra(dia: str) -> dict[str, Any]:
    """
    Executa o fluxo de compra para o dia informado.

    MODO SEGURO (padrão): seleciona setor/categoria e valida os preços, mas NÃO
    clica em comprar nem entra no checkout/PIX. Use para verificar que a
    automação encontra e seleciona a oferta certa, sem risco.

    Para ir até o checkout/PIX, primeiro chame liberar_pagamento(confirmar=True).

    - dia: "28/10", "30/10" ou "31/10".
    """
    return _sessao.preparar_compra(dia)


@mcp.tool()
def liberar_pagamento(confirmar: bool = False) -> dict[str, Any]:
    """
    Liga/desliga a trava que autoriza o fluxo a chegar ao checkout/PIX.

    - confirmar=True: LIGA a autorização (o próximo buscar_e_preparar_compra vai
      até o checkout e gera o PIX). ATENÇÃO: a partir daqui o fluxo pode efetuar
      ações reais de compra no site.
    - confirmar=False (padrão): DESLIGA (volta ao modo seguro).
    """
    return _sessao.permitir_pagamento(bool(confirmar))


@mcp.tool()
def obter_codigo_pix(dia: str = '', setor: str = '', categoria: str = '',
                     preco: int = 0) -> dict[str, Any]:
    """
    Lê o código PIX na tela atual (após o checkout, com pagamento liberado).

    Exige a trava de pagamento LIGADA (liberar_pagamento(confirmar=True)) e que o
    fluxo já esteja na tela do PIX. O código PIX é registrado e notificado pelo
    bot; aqui retornamos se foi encontrado.
    """
    return _sessao.obter_codigo_pix(dia=dia, setor=setor, categoria=categoria, preco=preco)


@mcp.tool()
def finalizar_pagamento(dia: str, confirmar: bool = False) -> dict[str, Any]:
    """
    TRAVA DE SEGURANÇA. Executa o fluxo REAL até o checkout/PIX para o dia dado.

    - Sem confirmar=True: RECUSA e não faz nada (retorna aviso). Este é o padrão.
    - Com confirmar=True: liga a autorização de pagamento e roda o fluxo real,
      que pode efetuar a compra no site e gerar o PIX.

    Mesmo com confirmar=True, o pagamento em si (pagar o PIX no banco) é manual —
    o sistema apenas gera o código. Use com consciência: a operação é sensível.
    """
    if not confirmar:
        return {
            'executado': False,
            'motivo': 'Recusado por segurança: chame novamente com confirmar=True '
                      'para autorizar o fluxo real até o PIX.',
            **_sessao.status(),
        }
    _sessao.permitir_pagamento(True)
    return _sessao.preparar_compra(dia)


if __name__ == '__main__':
    mcp.run()
