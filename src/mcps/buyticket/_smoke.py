"""Smoke test do servidor MCP: importa, lista tools e testa as de leitura pura."""
import asyncio
import servidor


def main():
    # ── Tools registradas ────────────────────────────────────────────────────
    tools = asyncio.run(servidor.mcp.list_tools())
    nomes = sorted(t.name for t in tools)
    print('TOOLS:', nomes)

    # Garante que as ferramentas de fila estão registradas
    assert 'check_queue_status' in nomes, 'check_queue_status não encontrada!'
    assert 'wait_in_queue'       in nomes, 'wait_in_queue não encontrada!'
    print('FILA_TOOLS_OK: check_queue_status e wait_in_queue registradas')

    # ── Leitura pura (sem navegador) ─────────────────────────────────────────
    print('LIMITES.maior_limite =', servidor.listar_limites()['maior_limite'])
    a = servidor.analisar_preco('Pista', 'inteira', 1500)
    print('ANALISE Pista/inteira/1500 =>', a['aprovado'], '| limite', a['limite'])
    print('STATUS =', servidor.status_sessao())

    # mapa_site() agora deve incluir seletores_fila
    mapa = servidor.mapa_site()
    print('MAPA_SITE.login_url =', mapa['login_url'])
    assert 'seletores_fila' in mapa, 'seletores_fila ausente no mapa_site!'
    assert 'ativo' in mapa['seletores_fila'], 'seletor "ativo" ausente em seletores_fila!'
    assert 'posicao' in mapa['seletores_fila'], 'seletor "posicao" ausente em seletores_fila!'
    print('MAPA_FILA_OK: seletores_fila =', list(mapa['seletores_fila'].keys()))

    # ── Trava de pagamento ───────────────────────────────────────────────────
    r = servidor.finalizar_pagamento('28/10', confirmar=False)
    assert r['executado'] is False, 'Trava de pagamento falhou!'
    print('TRAVA_OK: finalizar_pagamento sem confirmar recusou ->', r['motivo'][:40])

    # ── check_queue_status sem sessão deve lançar ErroSessao ─────────────────
    try:
        servidor.check_queue_status('28/10')
        print('WARN: check_queue_status sem sessão não lançou exceção (esperado ErroSessao)')
    except Exception as e:
        if 'sessão' in str(e).lower() or 'iniciar' in str(e).lower():
            print('QUEUE_STATUS_OK: sem sessão lança ErroSessao corretamente')
        else:
            print(f'WARN: exceção inesperada em check_queue_status: {e}')

    # ── wait_in_queue sem sessão deve lançar ErroSessao ──────────────────────
    try:
        servidor.wait_in_queue('28/10', timeout_min=1)
        print('WARN: wait_in_queue sem sessão não lançou exceção (esperado ErroSessao)')
    except Exception as e:
        if 'sessão' in str(e).lower() or 'iniciar' in str(e).lower():
            print('WAIT_QUEUE_OK: sem sessão lança ErroSessao corretamente')
        else:
            print(f'WARN: exceção inesperada em wait_in_queue: {e}')

    print('\nSMOKE_OK')


if __name__ == '__main__':
    main()
