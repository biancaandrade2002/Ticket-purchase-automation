"""
Harness E2E — executa N ciclos completos do fluxo BuyTicket (padrão: 5),
coleta telemetria via Cronometro e salva relatório de gargalos.

Cada ciclo usa um driver Chrome novo (isolamento total entre execuções).
O relatório final mostra min/méd/máx por etapa e marca gargalos (⚠️).

Uso:
    py run_e2e_benchmark.py               # 5 ciclos, dia 28/10
    py run_e2e_benchmark.py --ciclos 3    # 3 ciclos
    py run_e2e_benchmark.py --dia 30/10   # dia diferente
    py run_e2e_benchmark.py --ciclos 1    # smoke test rápido

Requer:
    .env configurado (BUYTICKET_EMAIL, BUYTICKET_SENHA, NTFY_TOPIC, dados pessoais)
    COMPRA_REAL=true no .env para execução real até o PIX
    Chrome instalado; ChromeDriver baixado automaticamente

Saída:
    - Relatório impresso no terminal
    - Arquivo telemetria_YYYYMMDD_HHMMSS.txt salvo no diretório do script
    - Screenshots de cada execução em screenshots/e2e_N_pix_*.png
"""

import argparse
import os
import sys
import time
from datetime import datetime

# Garante que o diretório do script está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import bot
from ticket_logic import Cronometro

# ============================================================
# EXECUÇÃO DE UM CICLO
# ============================================================


def executar_ciclo(driver, url, data_show, ciclo_num, dry_run=True):
    """
    Executa um ciclo completo: login → aba → fluxo de compra → PIX.

    dry_run=True (padrão): respeita DRY_RUN do bot (modo seguro).
    dry_run=False: força COMPRA_REAL para o ciclo (só usar se autorizado).

    Retorna dict com: ciclo, sucesso (bool), etapa_falha (str|None).
    """
    print(f'\n[E2E] === Ciclo {ciclo_num} | {data_show} | dry_run={dry_run} ===')
    resultado = {'ciclo': ciclo_num, 'sucesso': False, 'etapa_falha': None}

    # Aplica modo dry_run para o ciclo
    dry_run_original = bot.DRY_RUN
    if not dry_run:
        bot.DRY_RUN = False

    try:
        # Login
        print(f'[E2E] [{ciclo_num}] Login...')
        bot.buyticket_login(driver)

        # Abre aba do dia
        print(f'[E2E] [{ciclo_num}] Abrindo aba {data_show}...')
        aba = bot._abrir_aba_dia(driver, url, data_show)
        if not aba:
            resultado['etapa_falha'] = 'abrir_aba'
            print(f'[E2E] [{ciclo_num}] FALHA em abrir_aba')
            return resultado

        # Executa o fluxo de compra completo
        print(f'[E2E] [{ciclo_num}] Executando fluxo de compra...')
        sucesso = bot.buyticket_fluxo_compra(driver, data_show, url)

        resultado['sucesso'] = sucesso is True
        if sucesso is True:
            print(f'[E2E] [{ciclo_num}] SUCESSO - PIX gerado!')
            bot.screenshot(driver, f'e2e_{ciclo_num}_pix_{data_show.replace("/", "")}')
        elif sucesso == 'expirado':
            resultado['etapa_falha'] = 'pix_expirado'
            print(f'[E2E] [{ciclo_num}] PIX expirado')
        else:
            resultado['etapa_falha'] = 'fluxo_compra'
            print(f'[E2E] [{ciclo_num}] FALHA no fluxo_compra')

    except Exception as e:
        print(f'[E2E] [{ciclo_num}] ERRO: {e}')
        resultado['etapa_falha'] = f'excecao: {str(e)[:80]}'

    finally:
        bot.DRY_RUN = dry_run_original

    return resultado


# ============================================================
# MAIN
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description='E2E Benchmark BuyTicket — executa N ciclos completos e mede latência'
    )
    parser.add_argument('--ciclos', type=int, default=5,
                        help='Número de ciclos E2E (padrão: 5)')
    parser.add_argument('--dia', type=str, default='28/10',
                        help='Dia do show: 28/10, 30/10 ou 31/10 (padrão: 28/10)')
    parser.add_argument('--real', action='store_true',
                        help='Desativa DRY_RUN (execução real até o PIX). USE COM CUIDADO.')
    args = parser.parse_args()

    # Seleciona URL do dia
    if args.dia not in bot.DATAS_SHOW:
        print(f'ERRO: dia "{args.dia}" inválido. Use: {", ".join(bot.DATAS_SHOW)}')
        sys.exit(1)
    idx = bot.DATAS_SHOW.index(args.dia)
    url = bot.URLS_BUYTICKET[idx]
    data_show = bot.DATAS_SHOW[idx]
    dry_run = not args.real

    print(f'\n{"=" * 60}')
    print(f'  E2E BENCHMARK BuyTicket')
    print(f'  Ciclos  : {args.ciclos}')
    print(f'  Dia     : {data_show}')
    print(f'  Modo    : {"REAL (COMPRA REAL!)" if not dry_run else "DRY_RUN (seguro)"}')
    print(f'  CDP     : {"ativo" if bot.CDP_BLOQUEIO else "desativado"}')
    print(f'  Limite  : R${bot.MAIOR_LIMITE}')
    print(f'{"=" * 60}\n')

    if not dry_run:
        print('⚠️  ATENÇÃO: modo REAL ativo. Este ciclo pode efetuar compras reais.')
        print('   Pressione Ctrl+C nos próximos 5 segundos para cancelar.')
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print('\nCancelado pelo usuário.')
            sys.exit(0)

    # Verifica variáveis essenciais
    if not bot.BUYTICKET_EMAIL or not bot.BUYTICKET_SENHA:
        print('ERRO: BUYTICKET_EMAIL e BUYTICKET_SENHA devem estar no .env')
        sys.exit(1)

    # Baixa ChromeDriver se necessário
    if not bot.CHROMEDRIVER_PATH:
        print('[E2E] Baixando/verificando ChromeDriver...')
        from webdriver_manager.chrome import ChromeDriverManager
        bot.CHROMEDRIVER_PATH = ChromeDriverManager().install()
        print(f'[E2E] ChromeDriver: {bot.CHROMEDRIVER_PATH}')

    # Reseta telemetria
    Cronometro.resetar()
    resultados = []
    t_inicio_total = time.monotonic()

    # Executa os ciclos
    for ciclo in range(1, args.ciclos + 1):
        driver = None
        try:
            print(f'\n[E2E] Criando driver para ciclo {ciclo}/{args.ciclos}...')
            driver = bot.criar_driver()
            resultado = executar_ciclo(driver, url, data_show, ciclo, dry_run=dry_run)
            resultados.append(resultado)
        except KeyboardInterrupt:
            print(f'\n[E2E] Interrompido pelo usuário no ciclo {ciclo}.')
            break
        except Exception as e:
            print(f'[E2E] Falha crítica no ciclo {ciclo}: {e}')
            resultados.append({
                'ciclo': ciclo,
                'sucesso': False,
                'etapa_falha': f'driver: {str(e)[:60]}',
            })
        finally:
            if driver:
                try:
                    driver.quit()
                    print(f'[E2E] Driver do ciclo {ciclo} encerrado.')
                except Exception:
                    pass

    duracao_total = time.monotonic() - t_inicio_total

    # Relatório de telemetria
    relatorio = Cronometro.relatorio()
    print(f'\n{relatorio}')

    # Resumo dos ciclos
    print(f'\n{"=" * 60}')
    print(f'  RESUMO — {len(resultados)} ciclo(s) executado(s)')
    print(f'{"=" * 60}')
    sucessos = sum(1 for r in resultados if r['sucesso'])
    for r in resultados:
        status = '✅ SUCESSO' if r['sucesso'] else f'❌ FALHA  [{r["etapa_falha"]}]'
        print(f'  Ciclo {r["ciclo"]:>2}: {status}')
    print(f'\n  Taxa de sucesso : {sucessos}/{len(resultados)}')
    print(f'  Duração total   : {duracao_total:.1f}s')
    print(f'{"=" * 60}\n')

    # Salva relatório em arquivo
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome_arquivo = f'telemetria_{ts}.txt'
    try:
        with open(nome_arquivo, 'w', encoding='utf-8') as f:
            f.write(f'E2E Benchmark BuyTicket — {ts}\n')
            f.write(f'Ciclos: {len(resultados)} | Dia: {data_show} | '
                    f'Modo: {"REAL" if not dry_run else "DRY_RUN"}\n\n')
            f.write(relatorio)
            f.write(f'\n\nRESUMO\n')
            f.write(f'Taxa de sucesso: {sucessos}/{len(resultados)}\n')
            f.write(f'Duração total  : {duracao_total:.1f}s\n\n')
            for r in resultados:
                status = 'SUCESSO' if r['sucesso'] else f'FALHA ({r["etapa_falha"]})'
                f.write(f'Ciclo {r["ciclo"]}: {status}\n')
        print(f'Relatório salvo: {nome_arquivo}')
    except Exception as e:
        print(f'Aviso: não foi possível salvar o relatório ({e})')


if __name__ == '__main__':
    main()
