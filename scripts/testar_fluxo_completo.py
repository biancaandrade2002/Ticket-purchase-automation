"""
Teste completo do fluxo BuyTicket atualizado (set/2026).
Executa em DRY_RUN=True (modo seguro) por padrão — não compra nada.

Passos testados:
  1. Login (CPF 2 etapas)
  2. Abrir página do evento, clicar no dia 28/Out
  3. Ler preço do botão Comprar
  4. Ler setores disponíveis (ComboBox Tipo)
  5. Selecionar primeiro setor válido
  6. Ler categorias disponíveis (ComboBox Categoria)
  7. Selecionar primeira categoria válida
  8. Confirmar seletor do botão Comprar (não clica — DRY_RUN)
  9. Screenshot de cada etapa

Ao final imprime um relatório com ✓/✗ por etapa.
"""
import os, sys, time
from pathlib import Path

# Configura o caminho para importar bot.py e ticket_logic.py
ROOT = Path(__file__).resolve().parent.parent  # raiz do projeto
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

# Força DRY_RUN antes de importar bot (para não comprar acidentalmente)
os.environ.setdefault('DRY_RUN', 'true')

import bot
from selenium.webdriver.common.by import By

# ─────────────────────────────────────────────────────────────────
SAIDA = ROOT / 'capturas' / 'teste_fluxo'
SAIDA.mkdir(parents=True, exist_ok=True)
DIA = '28/10'

resultados = {}

def ok(etapa, detalhe=''):
    resultados[etapa] = ('✓', detalhe)
    print(f'  ✓ {etapa}: {detalhe}')

def falha(etapa, detalhe=''):
    resultados[etapa] = ('✗', detalhe)
    print(f'  ✗ {etapa}: {detalhe}')

def ss(driver, nome):
    try:
        driver.save_screenshot(str(SAIDA / f'{nome}.png'))
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────
print('\n' + '='*60)
print('TESTE DE FLUXO COMPLETO — BuyTicket (set/2026)')
print(f'Dia alvo: {DIA} | DRY_RUN: {bot.DRY_RUN}')
print('='*60 + '\n')

driver = None
try:
    # ── 1. Criar driver ──────────────────────────────────────────
    print('[1] Criando driver...')
    from webdriver_manager.chrome import ChromeDriverManager
    bot.CHROMEDRIVER_PATH = ChromeDriverManager().install()
    driver = bot.criar_driver()
    ok('criar_driver', f'Chrome iniciado')

    # ── 2. Login ─────────────────────────────────────────────────
    print('\n[2] Login (CPF + Senha)...')
    bot.buyticket_login(driver)
    url_pos_login = driver.current_url
    if 'buyticketbrasil.com' in url_pos_login and 'entrar' not in url_pos_login and 'login' not in url_pos_login:
        ok('login', f'URL pós-login: {url_pos_login}')
    else:
        falha('login', f'URL suspeita: {url_pos_login}')
    ss(driver, '01-pos-login')

    # ── 3. Verificar site correto ────────────────────────────────
    print('\n[3] Verificando domínio...')
    if bot._verificar_site_correto(driver, DIA):
        ok('verificar_site', 'buyticketbrasil.com ✓')
    else:
        falha('verificar_site', 'domínio errado detectado')

    # ── 4. Abrir aba do dia ──────────────────────────────────────
    print(f'\n[4] Abrindo aba do dia {DIA}...')
    url_base = bot.URL_EVENTO_BASE
    driver.get(url_base)
    time.sleep(3)
    bot._fechar_popup_aviso(driver)
    time.sleep(1)
    ss(driver, '02-evento-principal')

    url_sessao = bot._selecionar_dia_na_pagina(driver, DIA)
    time.sleep(4)
    bot._fechar_popup_aviso(driver)
    url_atual = driver.current_url
    ss(driver, '03-sessao-dia')

    if url_sessao and '/session/' in (url_sessao or ''):
        ok('abrir_sessao_dia', f'URL sessão: {url_atual[:80]}')
    else:
        falha('abrir_sessao_dia', f'URL sem /session/: {url_atual[:80]}')

    # ── 5. Ler preço do botão Comprar ─────────────────────────────
    print('\n[5] Lendo preço do botão Comprar...')
    time.sleep(2)
    preco = bot.buyticket_ler_preco_botao(driver)
    if preco and preco > 0:
        ok('ler_preco_botao', f'R$ {preco}')
    else:
        falha('ler_preco_botao', 'Preço não encontrado (talvez não haja oferta agora)')

    # ── 6. Ler setores disponíveis ────────────────────────────────
    print('\n[6] Lendo setores (ComboBox Tipo)...')
    setores = bot.buyticket_ler_setores_disponiveis(driver)
    ss(driver, '04-dropdown-tipo-aberto')
    if setores:
        ok('ler_setores', f'{len(setores)} setor(es): {[s["nome"] for s in setores]}')
    else:
        falha('ler_setores', 'Nenhum setor encontrado')

    # ── 7. Selecionar primeiro setor ──────────────────────────────
    setor_escolhido = None
    if setores:
        print(f'\n[7] Selecionando setor "{setores[0]["nome"]}"...')
        ok_tipo = bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_TIPO, setores[0]['nome'])
        ss(driver, '05-tipo-selecionado')
        if ok_tipo:
            ok('selecionar_tipo', setores[0]['nome'])
            setor_escolhido = setores[0]
        else:
            falha('selecionar_tipo', f'Falha ao confirmar "{setores[0]["nome"]}"')
    else:
        falha('selecionar_tipo', 'Pulado — sem setores')

    # ── 8. Ler categorias ─────────────────────────────────────────
    categorias = []
    if setor_escolhido:
        print('\n[8] Lendo categorias (ComboBox Categoria)...')
        categorias = bot.buyticket_ler_categorias_disponiveis(driver)
        ss(driver, '06-dropdown-categoria-aberto')
        if categorias:
            ok('ler_categorias', f'{len(categorias)} categoria(s): {[c["nome"] for c in categorias]}')
        else:
            falha('ler_categorias', 'Nenhuma categoria encontrada')

    # ── 9. Selecionar primeira categoria ──────────────────────────
    if categorias:
        print(f'\n[9] Selecionando categoria "{categorias[0]["nome"]}"...')
        ok_cat = bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_CATEGORIA, categorias[0]['nome'])
        ss(driver, '07-categoria-selecionada')
        if ok_cat:
            ok('selecionar_categoria', categorias[0]['nome'])
        else:
            falha('selecionar_categoria', f'Falha ao confirmar "{categorias[0]["nome"]}"')
    else:
        falha('selecionar_categoria', 'Pulado — sem categorias')

    # ── 10. Confirmar preço pós-seleção ───────────────────────────
    print('\n[10] Relendo preço após seleção...')
    time.sleep(1)
    preco_final = bot.buyticket_ler_preco_botao(driver)
    if preco_final:
        ok('preco_pos_selecao', f'R$ {preco_final}')
    else:
        falha('preco_pos_selecao', 'Preço não encontrado após seleção')
    ss(driver, '08-estado-final')

    # ── 11. Verificar botão Comprar visível ───────────────────────
    print('\n[11] Verificando botão Comprar...')
    btns_comprar = driver.find_elements(By.CSS_SELECTOR, bot._SEL_BTN_COMPRAR)
    btn_visivel = any(b.is_displayed() for b in btns_comprar)
    if btn_visivel:
        ok('btn_comprar_visivel', f'{len(btns_comprar)} botão(ões) encontrado(s) — DRY_RUN: não clicou')
    else:
        falha('btn_comprar_visivel', 'Botão Comprar não visível')

    # ── 12. Modo seguro confirmado ────────────────────────────────
    print('\n[12] Confirmando modo DRY_RUN...')
    if bot.DRY_RUN:
        ok('dry_run_ativo', 'Nenhuma compra real foi feita')
    else:
        falha('dry_run_ativo', 'ATENÇÃO: DRY_RUN está DESLIGADO')

except Exception as e:
    import traceback
    print(f'\n  ERRO FATAL: {e}')
    traceback.print_exc()
    resultados['erro_fatal'] = ('✗', str(e))
    if driver:
        try:
            driver.save_screenshot(str(SAIDA / 'ERRO.png'))
        except Exception:
            pass
finally:
    # ── Relatório final ───────────────────────────────────────────
    print('\n' + '='*60)
    print('RELATÓRIO DO TESTE')
    print('='*60)
    total = len(resultados)
    ok_count = sum(1 for v in resultados.values() if v[0] == '✓')
    for etapa, (status, detalhe) in resultados.items():
        print(f'  {status} {etapa}: {detalhe}')
    print(f'\nResultado: {ok_count}/{total} etapas OK')
    print('='*60)

    if driver:
        input('\nPressione ENTER para fechar o browser...')
        try:
            driver.quit()
        except Exception:
            pass
