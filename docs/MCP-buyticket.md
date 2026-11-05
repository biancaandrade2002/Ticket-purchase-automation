# MCP — Mapeamento de Contexto do BuyTicket

> **Última atualização:** outubro/2026 — otimização de velocidade pós-migração Bubble → Next.js  
> **Fonte da verdade** para `bot.py`, `ticket_logic.py` e `src/mcps/buyticket/`.  
> Todos os seletores marcados ✅ foram confirmados por compras reais com geração de PIX.

---

## Índice

1. [Visão geral do site](#1-visão-geral-do-site)
2. [Isolamento do Chrome](#2-isolamento-do-chrome)
3. [Fluxo completo](#3-fluxo-completo)
4. [Página: Login](#4-página-login)
5. [Página: Evento e Sessão](#5-página-evento-e-sessão)
6. [Seleção de Tipo e Categoria](#6-seleção-de-tipo-e-categoria)
7. [Página: Checkout](#7-página-checkout)
8. [Formulário de Cobrança](#8-formulário-de-cobrança)
9. [Página: PIX e Confirmação](#9-página-pix-e-confirmação)
10. [Regras de negócio](#10-regras-de-negócio)
11. [Catálogo de estados e erros](#11-catálogo-de-estados-e-erros)
12. [Benchmarks de velocidade](#12-benchmarks-de-velocidade)
13. [Servidor MCP](#13-servidor-mcp)
14. [Referência: ticket_logic.py](#14-referência-ticket_logicpy)
15. [Referência: bot.py](#15-referência-botpy)
16. [Checklist de manutenção](#16-checklist-de-manutenção)
17. [Apêndice: Migração Bubble → Next.js](#17-apêndice-migração-bubble--nextjs)

---

## Legenda

| Marca | Significado |
|-------|-------------|
| ✅ | Confirmado por compra real com geração de PIX |
| ⚠️ | Funciona mas frágil / a re-confirmar |
| ❌ | Testado — não existe ou não funciona |

---

## 1. Visão geral do site

| Item | Valor |
|------|-------|
| URL base | `https://buyticketbrasil.com` |
| Empresa | BuyTicket Desenvolvimento de Softwares S.A. — CNPJ 52.438.845/0001-67 |
| Modelo | Marketplace de revenda — preços definidos por terceiros |
| Tecnologia | **Next.js + CSS Modules** (migrado de Bubble.io em set/2026) |
| Login | `https://buyticketbrasil.com/entrar` — **2 etapas: CPF → Continuar → Senha → Entrar** |
| Credenciais `.env` | `BUYTICKET_CPF` (etapa 1) e `BUYTICKET_SENHA` (etapa 2) |
| Pagamento | PIX exclusivamente (Pix Copia e Cola + QR Code) |
| Instabilidade conhecida | Banner amarelo "Estamos com instabilidades na plataforma" — intermitente em out/2026 |

### Shows monitorados

| Dia | Data | URL de sessão fixa (set/2026) |
|-----|------|-------------------------------|
| 28/10 | Quarta | `https://buyticketbrasil.com/event/bts-2026worldtourarirang/session/e4ac9384-ccd7-4f29-82b8-7ba8677bc2ed` |
| 30/10 | Sexta | `https://buyticketbrasil.com/event/bts-2026worldtourarirang/session/ced30381-0cc4-4779-9c6b-ba05f44d086e` |
| 31/10 | Sábado | `https://buyticketbrasil.com/event/bts-2026worldtourarirang/session/7dbb3a2b-985d-4bf2-9d36-df893169e098` |

> URLs de sessão são fixas (UUIDs estáveis). Navegam direto para a tela de seleção de ingressos.  
> A URL base `/event/bts-2026worldtourarirang` ainda funciona mas exige clicar no botão do dia.

### ❌ URLs que não funcionam mais

| URL antiga | Motivo |
|-----------|--------|
| `/evento/bts%E2%80%932026worldtourarirang?data=...&evento_local=...` | Retorna "Evento não encontrado" |
| Qualquer URL com `?data=` e `&evento_local=` | Parâmetros removidos na migração |

---

## 2. Isolamento do Chrome

> ⚠️ O bot usa **incógnito** para não interferir com o Chrome pessoal do usuário.

```python
# em criar_driver() — bot.py
options.add_argument('--incognito')
```

**Regra:** nunca rodar `taskkill /F /IM chrome.exe` — mata o Chrome pessoal.  
Para encerrar o bot: `driver.quit()` ou `encerrar_sessao()`.

---

## 3. Fluxo completo

```
Iniciar sessão
    → Login (CPF → senha) [~13s rede]
    → Pop-up "Entendi"? → _fechar_popup_aviso()
    → Abrir URL de sessão do dia (direto /session/UUID)
    → Verificar domínio buyticketbrasil.com
    → Ler preço botão Comprar (V1) — se > MAIOR_LIMITE → monitorar
    → Ler setores → filtrar → selecionar Pista (DROPDOWN_TIPO=0)
    → Ler categorias → filtrar → selecionar Inteira (DROPDOWN_CATEGORIA=1)
    → Validar preço final (V2) — se > limite → recarregar
    → Clicar botão Comprar [button[class*="PriceActionButton"]]
    → Checkout (/checkout?listing=UUID)
        → Clicar "Continuar" (modal de reserva 10min)
        → Detectar e aguardar fila CrowdHandler (se presente)
        → Selecionar PIX [button[class*="PaymentMethodRow"]]
        → Preencher e-mail recebedor → "Salvar"
        → Clicar "Pagar com PIX" → sheet de cobrança abre
        → Preencher formulário (8 campos via JS + CEP char-a-char)
        → Clicar "Comprar agora · R$X"
    → Tela PIX
        → Detectar por "Aguardando pagamento" / "Tempo para pagar"
        → Extrair código PIX via JS scan
        → Notificar via ntfy.sh (push urgente)
    → Monitorar countdown (10 min)
    → Se expirou: nova tentativa automática (loop)
```

---

## 4. Página: Login

- **URL:** `https://buyticketbrasil.com/entrar`
- **Tecnologia:** Next.js — `<button type="submit">` reais

### Pop-up de aviso (intermitente)

| Elemento | Seletor | Status |
|----------|---------|--------|
| Botão "Entendi" | `//button[contains(normalize-space(.), 'Entendi')]` (XPATH) | ✅ |
| Função | `_fechar_popup_aviso(driver)` — chama após **todo** `driver.get()` | ✅ |

> Aparece tanto na tela de login quanto na página do evento.

### Etapa 1 — CPF

| Campo | Seletor CSS | Atributos | Status |
|-------|-------------|-----------|--------|
| Campo CPF | `input[autocomplete="username"]` | `type=text`, `inputmode=numeric` | ✅ |
| Botão Continuar | `button[type="submit"]` | texto "Continuar" | ✅ |
| URL após clicar | muda para `https://buyticketbrasil.com/login` | — | ✅ |

### Etapa 2 — Senha

| Campo | Seletor CSS | Atributos | Status |
|-------|-------------|-----------|--------|
| Campo senha | `input[type="password"][autocomplete="current-password"]` | `placeholder="Sua senha"` | ✅ |
| Envio | `Keys.ENTER` no campo senha (mais robusto que clicar botão) | — | ✅ |
| URL após login | `https://buyticketbrasil.com/` (home) | confirma sucesso | ✅ |

### Verificação de sessão ativa

Sessão CAIU quando URL contém `entrar` ou `/login` ou `input[autocomplete="username"]` presente.

### ❌ Seletores de login inválidos

| Seletor antigo | Motivo |
|----------------|--------|
| `input[type="email"][placeholder="Seu email"]` | Email substituído por CPF |
| `div.clickable-element` texto "Entrar na conta" | Era Bubble.io |

---

## 5. Página: Evento e Sessão

### URL de sessão: `/event/bts-2026worldtourarirang/session/UUID`

Tela principal de seleção de ingressos.

| Elemento | Seletor | Status | Observações |
|----------|---------|--------|-------------|
| ComboBox Tipo | `input[role="combobox"][placeholder*="tipo"]` | ✅ | `aria-expanded="false"` fechado |
| ComboBox Categoria | `input[role="combobox"][placeholder*="categoria"]` | ✅ | `disabled` até Tipo ser selecionado |
| Botão Comprar | `button[class*="PriceActionButton"]` | ✅ | Texto: `"R$ 2.800\nmenor preço\nComprar"` |
| Âncora de carregamento | ComboBox Tipo ou PriceActionButton | ✅ | Confirma SPA hidratada |

> **Atenção:** existe um `input[role="combobox"]` extra — a barra de busca global (índice 0).  
> **Sempre usar seletor com `placeholder*="tipo"` ou `placeholder*="categoria"`** para não confundir.

### HTML real dos ComboBoxes (out/2026)

```html
<!-- Tipo de Ingresso -->
<input role="combobox" class="ComboBox-module__G8Hy4G__input"
  placeholder="Selecione o tipo de ingresso"
  aria-expanded="false" type="text" value="">

<!-- Categoria (disabled até Tipo ser selecionado) -->
<input role="combobox" class="ComboBox-module__G8Hy4G__input"
  placeholder="Selecione a categoria" disabled=""
  aria-expanded="false" type="text" value="">
```

---

## 6. Seleção de Tipo e Categoria

### Setores disponíveis para BTS 2026

| Setor no site | Chave interna | Monitorado | Limite Inteira | Limite Meia |
|---------------|---------------|------------|----------------|-------------|
| `Pista` | `pista` | ✅ **FOCO PRINCIPAL** | R$ 5.000 | R$ 1.000 |
| `Arquibancada` | `arquibancada` | ✅ | R$ 600 | R$ 500 |
| `Cadeira Superior` | `cadeira_superior` | ✅ | R$ 900 | R$ 600 |
| `Cadeira Inferior` | `cadeira_inferior` | ✅ | R$ 1.100 | R$ 700 |
| `Camarote Clássico` | — | ❌ excluído | — | — |
| `Camarote Experience + Pista` | — | ❌ excluído | — | — |
| `Camarote Ídolos` | — | ❌ excluído | — | — |
| `Camarote Oitta` | — | ❌ excluído | — | — |
| `Camarote Raça` | — | ❌ excluído | — | — |
| `Camarote Stadium` | — | ❌ excluído | — | — |

> Camarotes **não estão no `MAPA_SETOR`** → `mapear_setor()` retorna `None` → excluídos automaticamente.

### Categorias disponíveis (Pista — confirmadas ao vivo, out/2026)

| Categoria no site | Chave interna | Preço típico | Botão Comprar |
|-------------------|---------------|--------------|---------------|
| `Inteira` | `inteira` | R$3.200–R$3.500 | ✅ **ativo** |
| `Meia Estudante` | `meia` | R$2.800–R$3.400 | ✅ ativo |
| `Meia Jovem Baixa Renda` | `meia` | R$2.800–R$3.350 | ✅ ativo |
| `Meia Idoso` | `meia` | R$0 (sem oferta) | ⚠️ desabilitado |
| `Meia PCD` | `meia` | R$0 | ⚠️ desabilitado |
| `Meia Até 21` | `meia` | R$0 | ⚠️ desabilitado |
| `Meia Professor` | `meia` | R$0 | ⚠️ desabilitado |
| `Meia Pessoas Tea` | `meia` | R$0 | ⚠️ desabilitado |
| `Acompanhante PCD` | `meia` | R$0 | ⚠️ desabilitado |
| `Meia Profissional da Saúde` | `meia` | R$0 | ⚠️ desabilitado |
| `Meia aposentado` | `meia` | R$0 | ⚠️ desabilitado |

### Prioridade de seleção

```
Pista Inteira → Pista Meia → Cadeira Inferior Inteira → Cadeira Inferior Meia
→ Cadeira Superior Inteira → Arquibancada Inteira
```

### Fluxo de abertura do ComboBox (como funciona)

1. `_obter_comboboxes(driver)` → retorna `[cb_tipo, cb_cat]` por placeholder
2. `_abrir_dropdown(driver, indice)` → clica + aguarda opções via `_wait_for_any`
3. `_ler_opcoes_combobox_aberto(driver)` → seletor `[class*="ComboBox"][class*="option"]`
4. `buyticket_selecionar_e_confirmar(...)` → digita nome, clica na opção, confirma via `value`

### Opções de ComboBox — seletores confirmados

| Seletor | Status | Observações |
|---------|--------|-------------|
| `[class*="ComboBox"][class*="option"]` | ✅ | Seletor primário — retorna opções visíveis |
| `[class*="ComboBox"][class*="item"]` | ✅ | Fallback — evitar com `chevron`, `box`, `field`, `label` |
| `[role="option"]` | ⚠️ | Fallback genérico |
| `ul[role="listbox"]` | ❌ | Não existe no novo layout |

### ❌ Seletores de dropdown inválidos

| Seletor antigo | Motivo |
|----------------|--------|
| `button[class*="dropdownHead"]` | Era Bubble.io |
| `button[class*="dropdownItem"]` | Era Bubble.io |
| `span[class*="itemName"]` | Era Bubble.io |

---

## 7. Página: Checkout

**URL:** `https://buyticketbrasil.com/checkout?listing=UUID`

### Etapas do checkout

```
1. Modal de reserva "Continuar"
   → button com texto "Continuar" (não button[type="submit"])
   → Confirma via _wait_for_url_change()

2. Fila CrowdHandler (se presente)
   → section#ch-active visível → aguarda _aguardar_sair_da_fila()
   → Polling a 100ms

3. Selecionar PIX
   → button[class*="PaymentMethodRow"] com texto "PIX" ✅
   → Confirmado via data-state="active" ou border-color CSS

4. Preencher e-mail do recebedor
   → button com texto "e-mail de receb" ou "adicionar e-mail" → clica → sheet abre
   → input[type="email"] → preencher → "Salvar"
   → _wait_until_gone('input[type="email"]') para confirmar fechamento

5. Clicar "Pagar com PIX"
   → button que começa com "pagar" (não disabled) ✅
   → Sheet de cobrança abre

6. Preencher formulário de cobrança (seção 8)

7. Clicar "Comprar agora · R$X"
   → button que começa com "comprar agora" (não disabled) ✅
   → JS fallback: document.querySelectorAll('button') com textContent 'comprar agora'
```

### Botões do checkout

| Botão | Como encontrar | Status |
|-------|----------------|--------|
| Continuar (modal) | `_btn_por_texto(driver, 'Continuar')` | ✅ |
| PIX (método) | `button[class*="PaymentMethodRow"]` com texto "PIX" | ✅ |
| Pagar com PIX | button que `.lower().startswith('pagar')` e não `disabled` | ✅ |
| Comprar agora | button que `.lower().startswith('comprar agora')` e não `disabled` | ✅ |

### Informações mostradas no checkout (coluna direita)

| Campo | Valor de exemplo |
|-------|-----------------|
| Categoria | "Inteira" |
| Tipo | "Pista" |
| Ticketeira | "Ticketmaster" |
| Ingresso | R$ 3.200,00 |
| Taxa de serviço (10%) | R$ 320,00 |
| **Valor total** | **R$ 3.520,00** |

---

## 8. Formulário de Cobrança

Aparece em um **sheet/drawer** ao clicar "Pagar com PIX". Detectar abertura por:  
`_wait_for(driver, 'input[placeholder*="00000"]', timeout=5)` — campo CEP com máscara.

### Campos do formulário (confirmados por screenshots reais — out/2026)

| # | Label visível | Como o bot preenche | Valor típico | Status |
|---|---------------|---------------------|--------------|--------|
| 1 | Nome completo | JS: label contém `'nome completo'` | `NOME_COMPLETO` do .env | ✅ |
| 2 | CPF | JS: label contém `'cpf'` | `CPF` do .env | ✅ |
| 3 | CEP | JS fill + char-a-char para ViaCEP | `CEP` do .env | ✅ |
| 4 | Endereço | JS: label contém `'endereço'` | preenchido pelo ViaCEP | ✅ |
| 5 | N° do endereço | JS: label contém `'n° do endereço'` | `NUMERO` do .env | ✅ |
| 6 | Complemento (opcional) | JS: label contém `'complemento'` | `COMPLEMENTO` do .env | ✅ |
| 7 | Bairro | JS: label contém `'bairro'` | preenchido pelo ViaCEP | ✅ |
| 8 | Cidade | JS: label contém `'cidade'` ou `'munic'` | preenchido pelo ViaCEP | ✅ |
| 9 | Estado (UF) | `select` nativo via `Select(sel).select_by_value(ESTADO)` | `ESTADO` do .env | ✅ |

> **Importante:** O campo "N° do endereço" tem label "N° do endereço".  
> Na chave JS `dados`, colocar `'n° do endereço'` **ANTES** de `'endereço'` — caso contrário,  
> a chave genérica `'endereço'` captura o campo errado (bug corrigido em out/2026).

### Comportamento do CEP

1. JS fill define o valor imediatamente mas **não dispara ViaCEP**
2. `_digitar_no_campo(driver, campo, CEP, char_a_char=True)` — 8 chars × 20ms = ~160ms
3. ViaCEP retorna em ~500–1500ms e preenche automaticamente: Endereço, Bairro, Cidade
4. O bot aguarda 1.5s para ViaCEP completar (necessário — API assíncrona)
5. Estado (UF) é um `<select>` nativo — preenchido separadamente após o ViaCEP

### Chave de ordem do JS (CRÍTICO para evitar match errado)

```javascript
// CORRETO — chaves específicas ANTES das genéricas
var dados = {
    'nome completo': ...,
    'cpf': ...,
    'cep': ...,
    '00000': ...,           // placeholder do CEP
    'n° do endereço': ...,  // ← DEVE vir ANTES de 'endereço'
    'nº do endereço': ...,
    'numero do end': ...,
    'número do end': ...,
    'número': ...,
    'endereço': ...,        // ← DEPOIS dos campos de número
    'complemento': ...,
    'bairro': ...,
    'cidade': ...,
    'munic': ...
};
```

### Variáveis do .env necessárias

| Variável | Regra / Formato | Observação |
|----------|-----------------|------------|
| `NOME_COMPLETO` | Nome completo do titular, como consta no documento de identidade. | Usado no campo "Nome completo" do formulário de cobrança via match de label. |
| `CPF` | 11 dígitos numéricos. Aceita com ou sem formatação (`000.000.000-00` ou `00000000000`). | O campo usa jQuery Mask — o bot digita caractere a caractere para acionar a máscara. |
| `CEP` | Exatamente 8 dígitos numéricos, sem hífen. | Ao digitar, o site chama o ViaCEP e preenche automaticamente Endereço, Bairro e Cidade (~1,5 s). O bot aguarda esse preenchimento antes de continuar. |
| `ENDERECO` | Logradouro (rua, avenida, etc.). | Normalmente sobrescrito pelo ViaCEP após o CEP. Usado como fallback se a API falhar. |
| `NUMERO` | Somente dígitos. **Não aceita letras, traços ou "s/n"**. | Corresponde ao campo "N° do endereço" — a chave JS deve aparecer antes de `'endereço'` para evitar match errado (ver seção de ordem de chaves acima). |
| `COMPLEMENTO` | Texto livre. Pode ficar em branco. | Campo opcional no formulário; o bot pula se o valor estiver vazio. |
| `BAIRRO` | Nome do bairro. | Normalmente sobrescrito pelo ViaCEP. Usado como fallback. |
| `CIDADE` | Nome da cidade. | Normalmente sobrescrito pelo ViaCEP. Usado como fallback. |
| `ESTADO` | Sigla da UF em 2 letras maiúsculas (ex.: `SP`, `MG`, `RJ`). | Preenchido via `Select(el).select_by_value(ESTADO)` — elemento `<select>` nativo, não é digitado. |
| `EMAIL` | Endereço de e-mail válido. | Informado na etapa "e-mail de recebedor" do checkout; o ingresso digital é enviado para este endereço. |

---

## 9. Página: PIX e Confirmação

### Tela de sucesso PIX — confirmada por screenshots reais (out/2026)

| Elemento | Como detectar | Status |
|----------|---------------|--------|
| Página PIX carregada | Texto `"aguardando pagamento"` ou `"tempo para pagar"` no `page_source` | ✅ |
| Timer countdown | Texto `"MM:SS"` ao lado de "Tempo para pagar" | ✅ |
| Seção código PIX | Texto `"Código PIX"` visível | ✅ |
| Código PIX | `<p>` ou `<span>` com texto iniciando em `00020` (completo no DOM, truncado visualmente) | ✅ |
| Botão copiar (ícone) | Pequeno `button` com ícone de clipboard ao lado do código | ✅ |
| Botão "Copiar código" | `button` com texto "Copiar código" no rodapé | ✅ |
| QR Code | `<img>` (carrega lento — não aguardar) | ⚠️ |

### HTML da tela PIX (estrutura observada — out/2026)

```html
<h1>Aguardando pagamento</h1>
<span>R$ 3.740,00</span>

<div>Tempo para pagar <span>09:54</span></div>

<section>
  <h2>Código PIX</h2>
  <p>Copie o código abaixo e cole no app do seu banco para pagar:</p>
  <div>
    <p>00020101021226910014br.gov.bcb.pix2569api.developer.btgpactual.com/...</p>
    <button title="Copiar"><!-- ícone clipboard --></button>
  </div>
</section>

<section>
  <h2>QR Code PIX</h2>
  <img src="data:image/png;base64,...">
</section>

<button>Copiar código</button>
```

### Extração do código PIX

```python
# Estratégia 1 — JS scan (primária, mais robusta)
code = driver.execute_script("""
    var tags = ['p','span','div','input','textarea','pre','code'];
    for (var t of tags) {
        var els = document.getElementsByTagName(t);
        for (var el of els) {
            var txt = (t==='input'||t==='textarea') ? el.value : el.textContent;
            txt = (txt||'').trim();
            if (txt.startsWith('00020') && txt.length > 50) return txt;
        }
    }
    return '';
""")

# Estratégia 2 — input[disabled] com value
# Estratégia 3 — regex no page_source: re.search(r'(0002\S{30,})', src)
```

### Palavras-chave exclusivas da tela PIX (NÃO aparecem no checkout)

| Keyword | Onde aparece | Usar para detectar PIX? |
|---------|-------------|-------------------------|
| `"aguardando pagamento"` | Só na tela PIX | ✅ **SIM** |
| `"tempo para pagar"` | Só na tela PIX | ✅ **SIM** |
| `"código pix"` | **Também** no checkout (método de pag.) | ❌ **NÃO** |
| `"copiar código"` | **Também** pode aparecer no checkout | ❌ **NÃO** |
| `"pix recomendado"` | Só no checkout | ❌ **NÃO** (inverso) |

### Após expiração do PIX

Página "Prazo de pagamento encerrado":
- Heading: "Prazo de pagamento encerrado"  
- Texto: "O prazo para pagar terminou. Estamos confirmando o status do pedido..."
- Botão: "Comprar novamente"
- **Ação do bot:** navegar de volta para URL de sessão e fazer nova tentativa

---

## 10. Regras de negócio

### Limites de preço

| Setor | Inteira (máx) | Meia (máx) |
|-------|---------------|------------|
| **Pista** | **R$ 5.000** | R$ 1.000 |
| Cadeira Inferior | R$ 1.100 | R$ 700 |
| Cadeira Superior | R$ 900 | R$ 600 |
| Arquibancada | R$ 600 | R$ 500 |

### Validação em 2 etapas (obrigatória)

**V1 — validação do botão inicial** (antes de qualquer seleção):
- `buyticket_ler_preco_botao()` lê `button[class*="PriceActionButton"]`
- Se `preco_botao > MAIOR_LIMITE (5000)` → aguarda, não seleciona nada

**V2 — validação final** (após selecionar Tipo + Categoria):
- Reler `button[class*="PriceActionButton"]` — este é o **preço real da oferta**
- Se `preco_botao > limite_categoria` → recarrega e tenta de novo
- Se `preco_botao ≤ limite_categoria` → **COMPRA**

---

## 11. Catálogo de estados e erros

### Estados de página

| Estado | Como detectar | Ação |
|--------|---------------|------|
| Logado | URL não tem `entrar`/`login` | Prosseguir |
| Sessão caída | URL tem `entrar`/`login` ou CPF input presente | `buyticket_login()` |
| Site errado | URL sem `buyticketbrasil.com` | Alerta urgente + navegar de volta |
| Pop-up "Entendi" | XPATH button com texto "Entendi" | `_fechar_popup_aviso()` |
| Sessão carregada | ComboBox Tipo presente | Prosseguir |
| Fila CrowdHandler | `section#ch-active` visível ou URL `wait.crowdhandler.com` | `_aguardar_sair_da_fila()` |
| Acesso bloqueado | `section#ch-blocked` visível | Notificar urgente + abortar |

### Erros no checkout

| Mensagem de erro | Como detectar | Causa | Ação do bot |
|------------------|---------------|-------|-------------|
| "Não conseguimos processar o pagamento agora. Tente de novo." | `page_source` lower | Servidor rejeitou o pagamento (instabilidade) | `'erro_pagamento'` → retry automático em 3s |
| "Algo deu errado. Tente de novo." | `page_source` lower | Idem | `'erro_pagamento'` → retry |
| "Esse ingresso já tem uma compra em andamento. Escolha outro." | Banner vermelho na tela PIX | PIX pendente de tentativa anterior | **Informativo apenas** — o PIX atual ainda está válido |
| "Prazo de pagamento encerrado" | `page_source` lower | PIX expirou sem pagamento | Retornar `'expirado'` → nova tentativa no loop |
| "Seu lugar está reservado" com timer | Timer visível no checkout | Reserva ativa (10min) | Normal — prosseguir |

### Palavras-chave de erro no page_source

```python
_ERRO_KW = [
    'não conseguimos processar',
    'nao conseguimos processar',
    'algo deu errado',
    'tente de novo',
]
# ATENÇÃO: verificar ANTES de confirmar que _SUCESSO_KW NÃO está presente
# Se _SUCESSO_KW estiver no source, estamos na tela PIX de sucesso — não abortar!
_SUCESSO_KW = ['aguardando pagamento', 'tempo para pagar']  # exclusivos da tela PIX
```

---

## 12. Benchmarks de velocidade

### Medições reais (out/2026) — timestamps dos logs

| Etapa | Antes (out/2026) | Depois (otimizado) | Savings |
|-------|------------------|--------------------|---------|
| Login (CPF + senha) | ~13s | ~12s | ~1s |
| Abrir sessão + popup | ~4s | ~2.5s | ~1.5s |
| Ler setores | ~6s | ~6s | — |
| Selecionar tipo (Pista) | ~1.5s | ~1s | ~0.5s |
| Ler categorias | ~6s | ~6s | — |
| Selecionar categoria (Inteira) | ~1.5s | ~1s | ~0.5s |
| Clicar Comprar + checkout start | ~6s | ~3s | ~3s |
| Modal "Continuar" | ~2s | ~1s | ~1s |
| Selecionar PIX + e-mail | ~4s | ~2s | ~2s |
| Sheet cobrança abertura | ~3.5s | ~1s | ~2.5s |
| Preencher formulário | ~5s | ~4s | ~1s |
| Clicar "Comprar agora" | ~2s | ~1s | ~1s |
| Detectar tela PIX | ~2s | ~1s | ~1s |
| Extrair código PIX | ~0s | ~0s | — |
| **TOTAL (login → PIX)** | **~55s** | **~42s** | **~13s** |

### Sleeps que DEVEM permanecer

| Sleep | Valor | Por que não pode reduzir |
|-------|-------|--------------------------|
| CEP char-a-char: `0.02s/char` | ~160ms total | jQuery Mask plugin — reduzir perde dígitos |
| ViaCEP autocomplete | `1.5s` | API assíncrona externa — não há como prever |
| Erro de pagamento retry | `3s` | Servidor precisa resetar estado |
| PIX poll interval | `1s` | Detectar tela PIX mais rápido possível |

---

## 13. Servidor MCP

**Arquivos:** `src/mcps/buyticket/servidor.py` e `src/mcps/buyticket/servico.py`  
**Transporte:** stdio | **SDK:** FastMCP | **Venv:** `src/mcps/buyticket/.venv`

### Modo seguro (padrão)

`DRY_RUN=True` — faz todo o fluxo até validar preços mas **não clica em Comprar**.  
Para ir até o PIX: `liberar_pagamento(confirmar=True)` primeiro.

### Loop de retry (modo produção)

O script `testar_ate_pix.py` implementa um loop automático:
1. Tenta comprar (navega → seleciona → checkout → PIX)
2. Se PIX gerado → mantém tela aberta + notifica
3. Se PIX expirou → aguarda 8s → tenta novamente automaticamente
4. Se erro → aguarda 16s → tenta novamente
5. Máximo de 50 tentativas (proteção)
6. CTRL+C para cancelar a qualquer momento

### Ferramentas disponíveis

| Ferramenta | O que faz |
|------------|-----------|
| `mapa_site()` | Retorna este documento |
| `listar_limites()` | Limites, mapeamentos, datas dos shows |
| `analisar_preco(setor, tipo, preco)` | Valida se está dentro do limite |
| `status_sessao()` | Estado atual: logado? trava? DRY_RUN? |
| `iniciar_sessao(headless)` | Abre Chrome incógnito, login, abre sessão dos 3 dias |
| `encerrar_sessao()` | Fecha o Chrome do bot |
| `consultar_ofertas(dia)` | Lê preço + setores + categorias sem comprar |
| `buscar_e_preparar_compra(dia)` | Fluxo completo em modo seguro |
| `liberar_pagamento(confirmar)` | Liga/desliga autorização para ir até o PIX |
| `finalizar_pagamento(dia, confirmar)` | Fluxo real até o PIX (exige `confirmar=True`) |
| `check_queue_status(dia)` | Snapshot da fila CrowdHandler |
| `wait_in_queue(dia, timeout_min)` | Polling 100ms até liberar ou timeout |
| `obter_codigo_pix(...)` | Lê o código PIX da tela atual |

### Registro no `mcp.json`

```json
"buyticket": {
  "type": "stdio",
  "command": "<caminho-do-projeto>/src/mcps/buyticket/.venv/Scripts/python.exe",
  "args": ["<caminho-do-projeto>/src/mcps/buyticket/servidor.py"],
  "cwd": "<caminho-do-projeto>",
  "timeout": 120000,
  "disabled": false,
  "autoApprove": ["mapa_site", "listar_limites", "analisar_preco", "status_sessao"]
}
```

---

## 14. Referência: `ticket_logic.py` — lógica pura

> Sem Selenium. Toda a lógica de negócio testável fica aqui.

### Constantes

| Constante | Valor | Uso |
|-----------|-------|-----|
| `LIMITES_PRECO` | ver seção 10 | Fonte única dos limites — editar só aqui |
| `PRIORIDADE_SETOR` | pista=0, cadeira_inferior=1, cadeira_superior=2, arquibancada=3 | Ordem de tentativa |
| `MAPA_SETOR` | 4 entradas; Camarotes ausentes | Regra de exclusão automática |
| `MAPA_CATEGORIA` | inteira + 10 tipos de meia | Idem |
| `MAIOR_LIMITE` | R$ 5.000 | Calculado dinamicamente de `LIMITES_PRECO` |
| `MAX_CICLOS` | 3 | Máx. reloads por setor |
| `MAX_RETRIES` | 3 | Máx. tentativas por etapa |

### Funções públicas

| Função | Retorno | Descrição |
|--------|---------|-----------|
| `extrair_preco(texto)` | `int\|None` | Parseia `"R$ 2.800"` → `2800` |
| `validar_botao_inicial(preco, maior_limite)` | `(bool, str)` | V1: botão Comprar vs MAIOR_LIMITE |
| `validar_valor_final(preco_botao, preco_cat)` | `(bool, str)` | V2: preço real do botão após seleção |
| `filtrar_setores_validos(setores, limites)` | `list[dict]` | Remove não-mapeados e acima do limite |
| `filtrar_categorias_validas(categorias, setor_key, limites)` | `list[dict]` | Idem para categorias |
| `mapear_setor(nome)` | `str\|None` | `"Pista"` → `"pista"`, Camarote → `None` |
| `mapear_categoria(nome)` | `str\|None` | `"Inteira"` → `"inteira"`, `"Meia Estudante"` → `"meia"` |

---

## 15. Referência: `bot.py` — orquestração Selenium

### Constantes de seletores

| Constante | Seletor | Descrição |
|-----------|---------|-----------|
| `_SEL_COMBOBOX_TIPO` | `input[role="combobox"][placeholder*="tipo"]` | ComboBox Tipo de Ingresso |
| `_SEL_COMBOBOX_CATEGORIA` | `input[role="combobox"][placeholder*="categoria"]` | ComboBox Categoria |
| `_SEL_BTN_COMPRAR` | `button[class*="PriceActionButton"]` | Botão Comprar (com preço) |
| `_SEL_BTN_DATA` | `button[class*="SessionDateRow"]` | Botões de data (página-mãe) |
| `DROPDOWN_TIPO` | `0` | Índice do ComboBox de Tipo |
| `DROPDOWN_CATEGORIA` | `1` | Índice do ComboBox de Categoria |

### Funções principais

| Função | Descrição |
|--------|-----------|
| `criar_driver()` | Chrome incógnito (--incognito). Nunca toca no Chrome pessoal. |
| `buyticket_login(driver)` | CPF → Continuar → Senha → ENTER. Pop-up se presente. |
| `_fechar_popup_aviso(driver)` | Fecha "Entendi". Chamar após todo `driver.get()`. |
| `_verificar_site_correto(driver)` | Verifica domínio. Alerta se errado. |
| `buyticket_ler_preco_botao(driver)` | Lê PriceActionButton → `int\|None` |
| `buyticket_ler_setores_disponiveis(driver)` | Abre ComboBox Tipo, lê, fecha. |
| `buyticket_ler_categorias_disponiveis(driver)` | Abre ComboBox Categoria, lê, fecha. |
| `buyticket_selecionar_e_confirmar(driver, indice, nome)` | Digita nome, clica opção, confirma via DOM. |
| `buyticket_checkout(driver, data, setor, categoria, preco)` | Fluxo completo checkout → PIX. |
| `buyticket_aguardar_pix(driver, ...)` | Poll 1s × 120s. Extrai código via JS scan. |
| `_tentar_extrair_pix(driver, src)` | JS scan + input[disabled] + regex. Retorna código ou None. |

### Helpers de tempo (todos event-driven)

| Helper | Comportamento |
|--------|---------------|
| `_wait_for(driver, sel, timeout)` | WebDriverWait até elemento visível |
| `_wait_until_gone(driver, sel, timeout)` | WebDriverWait até elemento sumir |
| `_wait_for_any(driver, sels, timeout)` | Polling 80ms — retorna o primeiro seletor que aparecer |
| `_wait_for_url_change(driver, url, timeout)` | WebDriverWait.url_changes |

---

## 16. Checklist de manutenção

### Quando o site mudar

- [ ] Capturar HTML: `copy(document.documentElement.outerHTML)` → salvar em `capturas/`
- [ ] Verificar `input[autocomplete="username"]` ainda é o campo CPF
- [ ] Verificar `button[class*="PriceActionButton"]` ainda é o botão Comprar
- [ ] Verificar `input[role="combobox"][placeholder*="tipo"]` ainda é o ComboBox
- [ ] Verificar `button[class*="PaymentMethodRow"]` ainda é o método PIX
- [ ] Verificar se `input[placeholder*="00000"]` ainda é âncora do sheet de cobrança
- [ ] Verificar palavras-chave exclusivas da tela PIX ("aguardando pagamento", "tempo para pagar")
- [ ] Atualizar `URLS_BUYTICKET` se UUIDs de sessão mudarem
- [ ] Atualizar `MAPA_SETOR` e `MAPA_CATEGORIA` em `ticket_logic.py` se novos setores surgirem

### Quando uma categoria nova aparecer

```python
# ticket_logic.py → MAPA_CATEGORIA
'novo nome exato': 'inteira',  # ou 'meia'

# ticket_logic.py → LIMITES_PRECO (se novo setor)
'novo_setor': {'inteira': 5000, 'meia': 1000},
```

---

## 17. Apêndice: Migração Bubble → Next.js (set/2026)

| O que mudou | Antes (Bubble) | Depois (Next.js) |
|-------------|----------------|-------------------|
| Login | 1 tela: email + senha | 2 etapas: CPF → senha |
| Campo identificador | `input[type="email"]` | `input[autocomplete="username"]` (CPF) |
| Botão login | `div.clickable-element` | `button[type="submit"]` |
| URL do evento | `/evento/slug?data=...` | `/event/slug/session/UUID` |
| Dropdown Tipo | `button[class*="dropdownHead"]` | `input[role="combobox"][placeholder*="tipo"]` |
| Dropdown Categoria | `button[class*="dropdownHead"]` | `input[role="combobox"][placeholder*="categoria"]` |
| Opções dropdown | `button[class*="dropdownItem"]` | `[class*="ComboBox"][class*="option"]` |
| Botão Comprar | `button:has-text("Comprar agora por R$...")` | `button[class*="PriceActionButton"]` |
| Método PIX | `input[type="radio"][value="PIX"]` | `button[class*="PaymentMethodRow"]` com texto "PIX" |
| Tela PIX | `#checkout_pix` | Sem ID fixo — detectar por "aguardando pagamento" + JS scan |
| Código PIX | `input[disabled]` com `value` | `<p>` com texto iniciando em `00020` (truncado via CSS) |
| Checkout billing | `input[placeholder*="..."]` | `input` sem placeholder — usar labels via JS |
| Isolamento Chrome | `--user-data-dir` (causava crash) | `--incognito` (seguro) |
| Barra de busca | não existia | `input[role="combobox"]` índice 0 — confundir com dropdowns de ingresso |
