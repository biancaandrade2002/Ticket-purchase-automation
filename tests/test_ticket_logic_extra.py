"""
Testes Extras — ticket_logic.py

Cobre lacunas não presentes em test_ticket_logic.py:
- _normalizar_valor: milhar, centavos, strings inválidas
- extrair_preco: nbsp HTML, formatos extremos, edge cases
- mapear_setor / mapear_categoria: trim, case, caracteres especiais
- filtrar_setores_validos: setor no limite exato, múltiplos mistos, sem preco
- filtrar_categorias_validas: setor_key inválido, limite exato, campos extras
- url_e_do_dia: fragmento de URL, parâmetros extras, dados idênticos
- deve_checar_login: zero de intervalo, valores negativos
- calcular_maior_limite: tabela de 1 setor, valores iguais
"""

import pytest
import ticket_logic as tl


# ═══════════════════════════════════════════════════════════════════════════════
# 1. _normalizar_valor (função interna)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizarValor:

    def test_milhar_com_ponto(self):
        assert tl._normalizar_valor('1.600') == 1600

    def test_milhar_duplo(self):
        assert tl._normalizar_valor('1.100.000') == 1100000

    def test_sem_separador(self):
        assert tl._normalizar_valor('900') == 900

    def test_com_centavos_virgula(self):
        assert tl._normalizar_valor('1.000,50') == 1000

    def test_com_centavos_sem_milhar(self):
        assert tl._normalizar_valor('900,00') == 900

    def test_so_centavos_zera(self):
        # '0,99' → parte inteira = '0' → 0
        assert tl._normalizar_valor('0,99') == 0

    def test_string_vazia_retorna_none(self):
        assert tl._normalizar_valor('') is None

    def test_string_nao_numerica_retorna_none(self):
        assert tl._normalizar_valor('abc') is None

    def test_espaco_em_branco_retorna_none(self):
        assert tl._normalizar_valor('   ') is None

    def test_numero_4_digitos_sem_separador(self):
        # "1000" não deve ser truncado para 100
        assert tl._normalizar_valor('1000') == 1000

    def test_numero_5_digitos(self):
        assert tl._normalizar_valor('12345') == 12345


# ═══════════════════════════════════════════════════════════════════════════════
# 2. extrair_preco — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtrairPreco:

    @pytest.mark.parametrize('texto,esperado', [
        ('R$ 1.600',          1600),
        ('R$1.600',           1600),
        ('R$\xa01.600',       1600),   # espaço não-quebrável
        ('R$&nbsp;1.600',     1600),   # entidade HTML
        ('R$ 1.000,00',       1000),   # milhar + centavos
        ('R$ 900,50',          900),   # centavos truncados
        ('R$ 1000',           1000),   # 4 dígitos sem separador
        ('R$ 4.100',          4100),   # maior limite exato
        ('Comprar R$ 3.300',  3300),   # texto antes do preço
        ('1.600',             1600),   # sem prefixo R$
    ])
    def test_formatos_validos(self, texto, esperado):
        assert tl.extrair_preco(texto) == esperado, f"texto={texto!r}"

    @pytest.mark.parametrize('texto', [
        None, '', 'Esgotado', 'Indisponível', 'abc', 'R$', '---',
    ])
    def test_invalidos_retornam_none(self, texto):
        assert tl.extrair_preco(texto) is None

    def test_nao_trunca_4_digitos_sem_ponto(self):
        # Regressão: "R$ 1000" já foi truncado para 100 pela regex antiga
        assert tl.extrair_preco('R$ 1000') == 1000
        assert tl.extrair_preco('R$ 1600') == 1600
        assert tl.extrair_preco('R$ 4100') == 4100

    def test_valor_com_espacos_extras(self):
        assert tl.extrair_preco('R$  600') == 600

    def test_multiplos_precos_no_texto_pega_primeiro(self):
        # Apenas o primeiro match é retornado
        resultado = tl.extrair_preco('R$ 600 ou R$ 900')
        assert resultado == 600


# ═══════════════════════════════════════════════════════════════════════════════
# 3. mapear_setor — trim e case
# ═══════════════════════════════════════════════════════════════════════════════

class TestMapearSetor:

    @pytest.mark.parametrize('nome,esperado', [
        ('Pista',                'pista'),
        ('pista',                'pista'),
        ('PISTA',                'pista'),
        ('Arquibancada',         'arquibancada'),
        ('ARQUIBANCADA',         'arquibancada'),
        ('Cadeira Superior',     'cadeira_superior'),
        ('CADEIRA SUPERIOR',     'cadeira_superior'),
        ('Cadeira Inferior',     'cadeira_inferior'),
        ('CADEIRA INFERIOR',     'cadeira_inferior'),
    ])
    def test_mapeamentos_validos(self, nome, esperado):
        assert tl.mapear_setor(nome) == esperado

    @pytest.mark.parametrize('nome', [
        None, '', 'Camarote VIP', 'Pista Premium', 'Golden Circle',
        'cadeira',  # prefixo incompleto
    ])
    def test_desconhecidos_retornam_none(self, nome):
        assert tl.mapear_setor(nome) is None

    def test_strip_de_espacos(self):
        # mapear_setor faz strip() antes de buscar no dict
        assert tl.mapear_setor('  pista  ') == 'pista'


# ═══════════════════════════════════════════════════════════════════════════════
# 4. mapear_categoria — trim e case
# ═══════════════════════════════════════════════════════════════════════════════

class TestMapearCategoria:

    @pytest.mark.parametrize('nome,esperado', [
        ('Inteira',         'inteira'),
        ('inteira',         'inteira'),
        ('INTEIRA',         'inteira'),  # mapear_categoria faz .lower() antes de buscar
        ('Meia Estudante',  'meia'),
        ('meia estudante',  'meia'),
        ('Meia Idoso',      'meia'),
        ('Meia',            'meia'),
        ('meia',            'meia'),
    ])
    def test_mapeamentos(self, nome, esperado):
        assert tl.mapear_categoria(nome) == esperado

    @pytest.mark.parametrize('nome', [
        None, '', 'Meia PCD', 'Meia Jovem Baixa Renda',
        'Meia Professor', 'Meia aposentado', 'Acompanhante PCD',
    ])
    def test_nao_mapeados_retornam_none(self, nome):
        assert tl.mapear_categoria(nome) is None

    def test_strip_de_espacos(self):
        assert tl.mapear_categoria('  meia  ') == 'meia'


# ═══════════════════════════════════════════════════════════════════════════════
# 5. filtrar_setores_validos — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestFiltrarSetoresExtra:

    def test_lista_vazia_retorna_vazia(self):
        assert tl.filtrar_setores_validos([]) == []

    def test_setor_no_limite_exato_aceito(self):
        # Pista inteira = 4500; preco_min == maior_limite_setor => aceito
        setores = [{'nome': 'Pista', 'preco_min': 4500}]
        validos = tl.filtrar_setores_validos(setores)
        assert len(validos) == 1

    def test_setor_acima_do_limite_rejeitado(self):
        setores = [{'nome': 'Arquibancada', 'preco_min': 601}]
        # Arquibancada: maior limite = max(600, 500) = 600; 601 > 600 → rejeitado
        assert tl.filtrar_setores_validos(setores) == []

    def test_mistura_validos_e_invalidos(self):
        setores = [
            {'nome': 'Camarote VIP',     'preco_min': 50},    # não mapeado
            {'nome': 'Arquibancada',     'preco_min': 9999},  # acima do limite
            {'nome': 'Pista',            'preco_min': None},  # sem preco
            {'nome': 'Cadeira Superior', 'preco_min': 500},   # válido
        ]
        validos = tl.filtrar_setores_validos(setores)
        assert len(validos) == 1
        assert validos[0]['key'] == 'cadeira_superior'

    def test_key_adicionada_no_resultado(self):
        setores = [{'nome': 'Pista', 'preco_min': 900}]
        validos = tl.filtrar_setores_validos(setores)
        assert 'key' in validos[0]
        assert validos[0]['key'] == 'pista'

    def test_limites_customizados_respeitados(self):
        custom = {'especial': {'inteira': 200, 'meia': 100}}
        # Setor 'especial' não está no MAPA_SETOR → ainda excluído pelo mapear_setor
        setores = [{'nome': 'especial', 'preco_min': 150}]
        assert tl.filtrar_setores_validos(setores, custom) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. filtrar_categorias_validas — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestFiltrarCategoriasExtra:

    def test_lista_vazia_retorna_vazia(self):
        assert tl.filtrar_categorias_validas([], 'pista') == []

    def test_setor_key_invalido_retorna_vazia(self):
        categorias = [{'nome': 'Inteira', 'preco': 500}]
        assert tl.filtrar_categorias_validas(categorias, 'inexistente') == []

    def test_limite_exato_aceito(self):
        # Arquibancada inteira = 600; preco == 600 → aceito
        categorias = [{'nome': 'Inteira', 'preco': 600}]
        validas = tl.filtrar_categorias_validas(categorias, 'arquibancada')
        assert len(validas) == 1

    def test_preco_negativo_rejeitado(self):
        categorias = [{'nome': 'Inteira', 'preco': -1}]
        assert tl.filtrar_categorias_validas(categorias, 'pista') == []

    def test_campos_extras_preservados(self):
        categorias = [{'nome': 'Inteira', 'preco': 500, 'badge': 'el_456'}]
        validas = tl.filtrar_categorias_validas(categorias, 'pista')
        assert validas[0].get('badge') == 'el_456'

    def test_key_e_limite_adicionados(self):
        categorias = [{'nome': 'Meia Estudante', 'preco': 900}]
        validas = tl.filtrar_categorias_validas(categorias, 'pista')
        assert validas[0]['key'] == 'meia'
        assert validas[0]['limite'] == 1000

    def test_multiplas_categorias_validas_retornadas(self):
        categorias = [
            {'nome': 'Inteira',       'preco': 900},
            {'nome': 'Meia Estudante','preco': 800},
            {'nome': 'Meia',          'preco': 750},
        ]
        validas = tl.filtrar_categorias_validas(categorias, 'pista')
        assert len(validas) == 3

    def test_limites_customizados(self):
        custom = {'pista': {'inteira': 100, 'meia': 50}}
        cats = [{'nome': 'Inteira', 'preco': 101}]
        assert tl.filtrar_categorias_validas(cats, 'pista', custom) == []
        cats2 = [{'nome': 'Inteira', 'preco': 100}]
        assert tl.filtrar_categorias_validas(cats2, 'pista', custom) != []


# ═══════════════════════════════════════════════════════════════════════════════
# 7. url_e_do_dia — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestUrlEDoDiaExtra:

    URL_28 = 'https://buyticketbrasil.com/evento/bts?data=1793242799000&cidade=SP'
    URL_30 = 'https://buyticketbrasil.com/evento/bts?data=1793415599000&cidade=SP'

    def test_mesma_url_retorna_true(self):
        assert tl.url_e_do_dia(self.URL_28, self.URL_28) is True

    def test_data_diferente_retorna_false(self):
        assert tl.url_e_do_dia(self.URL_30, self.URL_28) is False

    def test_url_atual_sem_data_retorna_false(self):
        assert tl.url_e_do_dia('https://buyticketbrasil.com/', self.URL_28) is False

    def test_url_atual_none_retorna_false(self):
        assert tl.url_e_do_dia(None, self.URL_28) is False

    def test_url_atual_vazia_retorna_false(self):
        assert tl.url_e_do_dia('', self.URL_28) is False

    def test_parametros_extras_na_url_atual_ok(self):
        # URL atual tem parâmetros extras além de data= — ainda é o mesmo dia
        url_com_extra = self.URL_28 + '&utm_source=bot'
        assert tl.url_e_do_dia(url_com_extra, self.URL_28) is True

    def test_sem_data_na_esperada_compara_caminho_base(self):
        base = 'https://buyticketbrasil.com/entrar'
        assert tl.url_e_do_dia(base + '?x=1', base) is True
        assert tl.url_e_do_dia('https://buyticketbrasil.com/outro', base) is False

    def test_url_esperada_none_nao_lanca_excecao(self):
        # Não deve lançar; comportamento: retorna False ou True dependendo da impl
        resultado = tl.url_e_do_dia(self.URL_28, None)
        assert isinstance(resultado, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. deve_checar_login — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeveChecarLoginExtra:

    def test_intervalo_zero_checa_sempre(self):
        # Com intervalo=0, qualquer diferença de tempo dispara checagem
        assert tl.deve_checar_login(agora=100, ultima_checagem=100, intervalo=0) is True

    def test_intervalo_customizado_muito_curto(self):
        assert tl.deve_checar_login(agora=10.1, ultima_checagem=10.0, intervalo=0.05) is True
        assert tl.deve_checar_login(agora=10.04, ultima_checagem=10.0, intervalo=0.05) is False

    def test_ultima_checagem_no_futuro_nao_checa(self):
        # Caso improvável mas defensivo: ultima_checagem maior que agora
        assert tl.deve_checar_login(agora=100, ultima_checagem=200) is False

    def test_valores_grandes_de_monotonic(self):
        # Simula bot rodando por horas (valores grandes de time.monotonic)
        assert tl.deve_checar_login(agora=86400, ultima_checagem=82800) is True   # 1h depois
        assert tl.deve_checar_login(agora=86400, ultima_checagem=86000) is False  # 400s atrás


# ═══════════════════════════════════════════════════════════════════════════════
# 9. calcular_maior_limite — variações
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalcularMaiorLimite:

    def test_tabela_real(self):
        assert tl.calcular_maior_limite() == 4500

    def test_tabela_de_um_setor(self):
        custom = {'unico': {'inteira': 300, 'meia': 150}}
        assert tl.calcular_maior_limite(custom) == 300

    def test_valores_iguais(self):
        custom = {'a': {'inteira': 500, 'meia': 500},
                  'b': {'inteira': 500, 'meia': 500}}
        assert tl.calcular_maior_limite(custom) == 500

    def test_meia_maior_que_inteira_retorna_meia(self):
        # Tabela hipotética onde meia > inteira (não deve ocorrer na prática)
        custom = {'x': {'inteira': 100, 'meia': 200}}
        assert tl.calcular_maior_limite(custom) == 200

    def test_varios_setores_retorna_maximo_global(self):
        custom = {
            'a': {'inteira': 600,  'meia': 500},
            'b': {'inteira': 900,  'meia': 600},
            'c': {'inteira': 1100, 'meia': 700},
            'd': {'inteira': 4100, 'meia': 1000},
        }
        assert tl.calcular_maior_limite(custom) == 4100
