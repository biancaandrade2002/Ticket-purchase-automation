"""
Teste completo do fluxo BuyTicket até o PIX — com loop automático.

Fica tentando até:
  - PIX ser gerado e a URL mudar (pagamento confirmado)  →  sai com sucesso
  - Usuário pressionar CTRL+C  →  sai sem comprar
  - MAX_TENTATIVAS atingidas  →  sai com aviso

A cada tentativa:
  1. Navega para a URL da sessão 28/10
  2. Seleciona Pista Inteira (dentro do limite R$5.000)
  3. Clica Comprar → checkout → PIX
  4. Se PIX expirou ou deu erro → aguarda PAUSA_ENTRE_TENTATIVAS e tenta de novo
"""
import os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # raiz do projeto
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv()

os.environ['DRY_RUN'] = 'false'
os.environ['COMPRA_REAL'] = 'true'

import bot
import ticket_logic
from selenium.webdriver.common.by import By

SAIDA = ROOT / 'capturas' / 'teste_pix'
SAIDA.mkdir(parents=True, exist_ok=True)

DIA = '30/10'
URL_SESSAO = bot.URLS_BUYTICKET[1]          # URL fixa da sessão 30/10
MAX_TENTATIVAS = 50                          # proteção contra loop infinito
PAUSA_ENTRE_TENTATIVAS = 8                   # segundos entre retentativas


# ──────────────────────────────────────────────────────────────────────────────
def ss(driver, nome):
    try:
        path = str(SAIDA / f'{nome}.png')
        driver.save_screenshot(path)
        print(f'  📸 {path}')
    except Exception:
        pass


def _tentar_compra(driver, tentativa: int) -> str:
    """
    Executa uma tentativa completa: navega → seleciona → compra → checkout.
    Retorna:
      'pago'      — URL mudou após PIX (pagamento confirmado)
      'expirado'  — PIX gerado mas expirou sem pagamento
      'erro'      — qualquer falha antes do checkout completar
    """
    print(f'\n{"="*60}')
    print(f'TENTATIVA {tentativa} — {DIA} | {time.strftime("%H:%M:%S")}')
    print('='*60)

    # ── Navegar para sessão ───────────────────────────────────────
    print(f'[A] Abrindo sessão {DIA}...')
    driver.get(URL_SESSAO)
    time.sleep(3)
    bot._fechar_popup_aviso(driver)
    time.sleep(1)
    print(f'  URL: {driver.current_url}')

    if not bot._verificar_site_correto(driver, DIA):
        print('  ✗ Site errado detectado. Abortando tentativa.')
        return 'erro'

    # ── Ler preço ─────────────────────────────────────────────────
    time.sleep(2)
    preco_botao = bot.buyticket_ler_preco_botao(driver)
    ok_v1, motivo_v1 = bot.validar_botao_inicial(preco_botao, bot.MAIOR_LIMITE)
    print(f'[B] Preço botão: R${preco_botao} → {"OK" if ok_v1 else motivo_v1}')
    if not ok_v1:
        return 'erro'

    # ── Ler setores ───────────────────────────────────────────────
    setores = bot.buyticket_ler_setores_disponiveis(driver)
    setores_validos = bot.filtrar_setores_validos(setores, bot.LIMITES_PRECO)
    print(f'[C] Setores válidos: {[s["nome"] for s in setores_validos]}')
    if not setores_validos:
        print('  Nenhum setor no limite.')
        return 'erro'

    setor = setores_validos[0]

    # ── Selecionar setor ──────────────────────────────────────────
    print(f'[D] Selecionando: {setor["nome"]}...')
    if not bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_TIPO, setor['nome']):
        print('  ✗ Falha ao selecionar tipo.')
        return 'erro'

    # ── Ler categorias ────────────────────────────────────────────
    time.sleep(0.5)  # breve espera após seleção de tipo
    setor_key = ticket_logic.mapear_setor(setor['nome'])
    categorias = bot.buyticket_ler_categorias_disponiveis(driver)
    cats_validas = bot.filtrar_categorias_validas(categorias, setor_key, bot.LIMITES_PRECO)
    print(f'[E] Categorias válidas: {[c["nome"] for c in cats_validas]}')
    if not cats_validas:
        print('  Nenhuma categoria no limite.')
        return 'erro'

    inteiras = [c for c in cats_validas if c.get('key') == 'inteira']
    meias    = [c for c in cats_validas if c.get('key') == 'meia']
    if inteiras:
        cat = min(inteiras, key=lambda c: c.get('preco') or 999999)
    elif meias:
        cat = min(meias, key=lambda c: (c.get('preco') in (None, 0), c.get('preco') or 999999))
    else:
        cat = cats_validas[0]

    # ── Selecionar categoria ──────────────────────────────────────
    print(f'[F] Selecionando: {cat["nome"]} R${cat["preco"]}...')
    if not bot.buyticket_selecionar_e_confirmar(driver, bot.DROPDOWN_CATEGORIA, cat['nome']):
        print('  ✗ Falha ao selecionar categoria.')
        return 'erro'

    # ── Validar preço ─────────────────────────────────────────────
    preco_final = bot.buyticket_ler_preco_botao(driver)
    ok_v2, motivo_v2 = bot.validar_valor_final(preco_final, cat['preco'])
    print(f'[G] Preço final: R${preco_final} → {"OK" if ok_v2 else "divergente"}')
    if not ok_v2:
        return 'erro'

    # V3: limite configurado — garante que o preço real (do botão) não excede o limite
    limite_cat = cat.get('limite', 0)
    if limite_cat and preco_final and preco_final > limite_cat:
        print(f'  ❌ V3: preço R${preco_final} > limite configurado R${limite_cat}. Pulando.')
        return 'erro'
    print(f'  V3: R${preco_final} ≤ R${limite_cat} → OK')

    # ── Clicar Comprar ────────────────────────────────────────────
    print(f'[H] Clicando COMPRAR (R${preco_final})...')
    if tentativa == 1:
        print('     Última chance para cancelar (CTRL+C) — 3 segundos...')
        time.sleep(3)
    else:
        time.sleep(1)

    btns = driver.find_elements(By.CSS_SELECTOR, bot._SEL_BTN_COMPRAR)
    btn = next((b for b in btns if b.is_displayed() and 'Comprar' in b.text), None)
    if not btn:
        print('  ✗ Botão Comprar não encontrado.')
        return 'erro'

    driver.execute_script('arguments[0].click();', btn)
    bot.notificar('COMPRANDO!',
                  f'T{tentativa} | {DIA}: R${preco_final} {setor["nome"]}/{cat["nome"]}',
                  'urgent')
    time.sleep(1)  # mínimo para a navegação começar
    print(f'  URL: {driver.current_url}')

    # ── Checkout ──────────────────────────────────────────────────
    print('[I] Executando checkout + PIX...')
    resultado = bot.buyticket_checkout(
        driver, DIA, setor['nome'], cat['nome'], preco_final
    )
    ss(driver, f'tentativa{tentativa:02d}-final')

    if resultado is True:
        return 'pago'
    elif resultado == 'expirado':
        return 'expirado'
    else:
        # erro_pagamento ou qualquer outro resultado
        return 'erro'


# ──────────────────────────────────────────────────────────────────────────────
print('\n' + '='*60)
print('LOOP DE COMPRA — BuyTicket (set/2026)')
print(f'Dia: {DIA} | URL: {URL_SESSAO[:70]}')
print(f'Limite Pista Inteira: R$ {bot.LIMITES_PRECO["pista"]["inteira"]}')
print(f'Máx tentativas: {MAX_TENTATIVAS} | Pausa: {PAUSA_ENTRE_TENTATIVAS}s')
print('='*60)
print('\n⚠️  PAGAMENTO LIBERADO — este script pode efetuar compras reais!')
print('     Pressione CTRL+C agora para cancelar.')
print('     Aguardando 5 segundos...\n')
time.sleep(5)

driver = None
try:
    # ── Iniciar driver e login (apenas uma vez) ───────────────────
    print('[1] Iniciando Chrome...')
    CHROMEDRIVER_CACHE = os.path.join(
        os.environ.get('USERPROFILE', os.path.expanduser('~')),
        r'.wdm\drivers\chromedriver\win64'
    )
    _cd_path = None
    for _root, _dirs, _files in os.walk(CHROMEDRIVER_CACHE):
        for _f in _files:
            if _f == 'chromedriver.exe':
                _cd_path = os.path.join(_root, _f)
    if not _cd_path:
        from webdriver_manager.chrome import ChromeDriverManager
        _cd_path = ChromeDriverManager().install()
    bot.CHROMEDRIVER_PATH = _cd_path
    print(f'  ChromeDriver: {_cd_path}')
    driver = bot.criar_driver()
    print('  ✓ Chrome iniciado')

    print('\n[2] Login (CPF + Senha)...')
    bot.buyticket_login(driver)
    print('  ✓ Login realizado')

    # ── Loop principal ────────────────────────────────────────────
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resultado = _tentar_compra(driver, tentativa)

            if resultado == 'pago':
                print(f'\n{"*"*60}')
                print(f'✓✓✓ COMPRA CONCLUÍDA na tentativa {tentativa}!')
                print('    Verifique as notificações push para o código PIX.')
                print('    Pague o PIX no app do banco agora.')
                print('*'*60)
                bot.notificar(
                    '✓ PIX PAGO — CONCLUÍDO!',
                    f'Compra confirmada na tentativa {tentativa} para {DIA}.',
                    'urgent'
                )
                break

            elif resultado == 'expirado':
                print(f'\n⏰ Tentativa {tentativa}: PIX expirou sem pagamento.')
                if tentativa < MAX_TENTATIVAS:
                    print(f'   Próxima tentativa em {PAUSA_ENTRE_TENTATIVAS}s...')
                    bot.notificar(
                        f'PIX expirou (T{tentativa})',
                        f'{DIA}: tentando novamente em {PAUSA_ENTRE_TENTATIVAS}s...'
                    )
                    time.sleep(PAUSA_ENTRE_TENTATIVAS)

            else:  # 'erro'
                print(f'\n✗ Tentativa {tentativa}: erro antes do PIX.')
                if tentativa < MAX_TENTATIVAS:
                    print(f'   Próxima tentativa em {PAUSA_ENTRE_TENTATIVAS * 2}s...')
                    time.sleep(PAUSA_ENTRE_TENTATIVAS * 2)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            import traceback
            print(f'\n⚠️  Tentativa {tentativa} com exceção: {e}')
            traceback.print_exc()
            if tentativa < MAX_TENTATIVAS:
                print(f'   Retomando em {PAUSA_ENTRE_TENTATIVAS * 2}s...')
                time.sleep(PAUSA_ENTRE_TENTATIVAS * 2)

    else:
        print(f'\n✗ Máximo de {MAX_TENTATIVAS} tentativas atingido.')
        bot.notificar('Loop encerrado', f'{DIA}: {MAX_TENTATIVAS} tentativas sem sucesso.')

except KeyboardInterrupt:
    print('\n\nCancelado pelo usuário.')
except Exception as e:
    import traceback
    print(f'\nERRO FATAL: {e}')
    traceback.print_exc()
    if driver:
        try:
            driver.save_screenshot(str(SAIDA / 'ERRO-fatal.png'))
        except Exception:
            pass
finally:
    if driver:
        try: driver.quit()
        except: pass
