# 🎫 BTS Ticket Monitor — BuyTicket + Ticketmaster

> ⚠️ **Aviso:** Este projeto foi desenvolvido para **fins educacionais e de estudo** de automação web com Python/Selenium. Não é destinado ao uso em produção ou para obter vantagem indevida em processos de compra. Respeite os termos de uso das plataformas.

Automação em Python + Selenium que monitora o **BuyTicket** e o **Ticketmaster** simultaneamente e realiza a compra de ingressos assim que o preço entra dentro do limite configurado.

O projeto é dividido em duas camadas:
- **`bot.py`** — orquestra o navegador (login, polling, seleção, checkout, PIX)
- **`ticket_logic.py`** — lógica pura testável sem navegador (limites, parsing, filtros, validações)

## 🛠️ Stack

| Camada | Tecnologia |
|--------|-----------|
| Automação web | Python 3.10+ · Selenium 4 · undetected-chromedriver |
| Lógica de negócio | Python puro (sem dependências externas) |
| Testes | pytest · hypothesis (property-based testing) |
| Notificações | ntfy.sh (push), Gmail, Telegram, Twilio |
| Integração IA | FastMCP (Model Context Protocol) |

## 🚀 Como Usar

### 1. Instalar dependências

```powershell
py -m pip install -r requirements.txt
```

### 2. Configurar

```powershell
Copy-Item .env.example .env
```

Edite o `.env` com:
- Credenciais do BuyTicket (`BUYTICKET_CPF`, `BUYTICKET_SENHA`)
- Credenciais do Ticketmaster (`TICKETMASTER_EMAIL`, `TICKETMASTER_SENHA`)
- Dados pessoais para preenchimento automático do checkout
- Canal de notificação (recomendado: ntfy.sh — zero cadastro)

### 3. Iniciar

```powershell
py bot.py
```

Ou via atalho Windows com watchdog (reinício automático após standby/crash):

```
scripts\INICIAR_BOT.bat
```

### 4. Testar sem risco de compra (DRY_RUN)

```powershell
# Faz todo o fluxo mas NÃO clica em Comprar
py scripts\testar_fluxo_completo.py
```

Ou defina `DRY_RUN=true` no `.env` e rode `py bot.py` normalmente.

### 5. Rodar os testes

```powershell
py -m pytest
```

## 💰 Limites de Preço

Configurados em `ticket_logic.py` → `LIMITES_PRECO`. O bot só compra quando o preço está **≤ ao limite** do setor/categoria.

> **Configuração atual: SEM LIMITE** — `LIMITES_PRECO` está com valor `999_999` em todos os setores, ou seja, o bot aceita qualquer preço. Para ativar limites reais, edite os valores em `ticket_logic.py`.

Exemplo com limites ativos:

| Setor | Inteira (máx) | Meia (máx) |
|-------|---------------|------------|
| Arquibancada | R$ 600 | R$ 500 |
| Cadeira Superior | R$ 900 | R$ 600 |
| Cadeira Inferior | R$ 1.100 | R$ 700 |
| Pista | R$ 1.600 | R$ 1.000 |

## 🔔 Notificações

Recomendado: **ntfy.sh** (gratuito, sem cadastro).

1. Instale o app "ntfy" no celular
2. Inscreva-se no tópico configurado em `NTFY_TOPIC`
3. Receba push notification a cada etapa (ingresso encontrado, PIX gerado, etc.)

## 📁 Estrutura do Projeto

```
BTS-Ticket/
├── bot.py                        # Motor principal — monitores BuyTicket e Ticketmaster
├── ticket_logic.py               # Lógica pura: limites, parsing, filtros, validações
├── watchdog.py                   # Reinício automático após standby, crash ou erro de rede
├── requirements.txt              # Dependências Python
├── .env.example                  # Template de configuração (copie para .env)
│
├── tests/                        # Testes unitários e de integração
│   ├── test_ticket_logic.py      # Lógica pura (limites, setores, categorias)
│   ├── test_bot_fluxo.py         # Fluxo de seleção com driver simulado
│   ├── test_properties.py        # Testes de propriedade (Hypothesis)
│   ├── test_regras_negocio.py    # Regras de negócio
│   └── ...                       # Demais testes de regressão e integração
│
├── scripts/                      # Utilitários e scripts de demonstração
│   ├── testar_fluxo_completo.py  # Dry-run passo a passo (não compra)
│   ├── testar_ate_pix.py         # Loop real até PIX ser gerado
│   ├── extrair_pix.py            # Extrai código PIX de Chrome já aberto
│   ├── run_e2e_benchmark.py      # Mede tempo de cada etapa do fluxo
│   ├── INICIAR_BOT.bat           # Atalho Windows: inicia com watchdog
│   └── TESTE_SEGURO_BOT.bat      # Atalho Windows: modo simulação (DRY_RUN)
│
├── src/mcps/buyticket/           # Servidor MCP — expõe o bot como ferramentas de IA
│   ├── servidor.py               # Ferramentas MCP (FastMCP, stdio)
│   └── servico.py                # Orquestra bot.py via SessaoBuyTicket
│
└── docs/
    └── MCP-buyticket.md          # Documentação completa: seletores, fluxo, benchmarks
```

## ⚙️ Como Funciona

1. **Monitoramento paralelo**: BuyTicket e Ticketmaster rodam em threads separadas.
2. **Uma aba por data (BuyTicket)**: abre uma aba persistente para cada show (28/10, 30/10, 31/10) e alterna com `switch_to`, sem renavegar do zero.
3. **Checagem periódica de login**: a cada `INTERVALO_CHECK_LOGIN_S` segundos verifica a sessão e refaz login automaticamente se ela caiu.
4. **Validação em 2 etapas**: V1 valida o botão inicial, V2 confirma o preço real após seleção de setor/categoria.
5. **Seleção resiliente**: seleciona setor e categoria por prioridade, com confirmação no DOM e retry (3 tentativas por etapa).
6. **Checkout automático**: seleciona PIX, preenche formulário via JS e conclui a compra.
7. **Notificações**: push a cada etapa relevante, incluindo o código PIX.

### Configurações de tempo (no `.env`)

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `INTERVALO_CHECK_MS` | `3000` | Intervalo entre verificações de preço (ms) |
| `INTERVALO_CHECK_LOGIN_S` | `3600` | Intervalo entre checagens de sessão/login (s) |

## ⚠️ Observações

- Configure `DRY_RUN=true` no `.env` para testar todo o fluxo sem risco de compra real
- Mantenha o `.env` seguro e nunca o compartilhe ou comite
- O modo `HEADLESS=false` permite acompanhar o navegador em tempo real
- Tenha PIX configurado no seu banco para pagamento rápido

## 🛑 Parar

```
Ctrl+C
```
