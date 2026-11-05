# Servidor MCP — BuyTicket

Expõe a automação do BuyTicket como ferramentas MCP (Model Context Protocol) para um
assistente de IA. Reaproveita `bot.py` (Selenium) e `ticket_logic.py` da raiz do projeto.

- `servidor.py` — define as ferramentas MCP (FastMCP, stdio).
- `servico.py` — classe `SessaoBuyTicket`: mantém o navegador e o estado; orquestra o `bot.py`.
- `requirements-mcp.txt` — dependências (SDK `mcp` + as da automação).

## Segurança (leia antes de usar)

O PIX é **irreversível**. O servidor opera em **modo seguro** por padrão: o fluxo seleciona a
oferta e valida preços, mas **não compra**. Para chegar ao checkout/PIX é preciso autorizar
explicitamente com `liberar_pagamento(confirmar=True)` ou `finalizar_pagamento(dia, confirmar=True)`.
Sem `confirmar=True`, `finalizar_pagamento` recusa. Pagar o PIX (no banco) continua manual.

## Instalação

```powershell
py -m venv .venv
.venv/Scripts/python -m pip install -r requirements-mcp.txt
```

O `.env` da raiz do projeto precisa ter credenciais e dados pessoais (ver `../../../.env.example`).

## Registro no mcp.json

O arquivo `~/.kiro/settings/mcp.json` é protegido; adicione manualmente dentro de `mcpServers`
(começa desabilitado — habilite quando quiser):

```json
"buyticket": {
  "type": "stdio",
  "command": "<caminho-do-projeto>/src/mcps/buyticket/.venv/Scripts/python.exe",
  "args": ["<caminho-do-projeto>/src/mcps/buyticket/servidor.py"],
  "cwd": "<caminho-do-projeto>",
  "timeout": 120000,
  "disabled": true,
  "autoApprove": ["mapa_site", "listar_limites", "analisar_preco", "status_sessao"]
}
```

> Substitua `<caminho-do-projeto>` pelo caminho absoluto onde você clonou o repositório.
> Exemplo Windows: `C:/Users/seu-usuario/Desktop/Ticket`
> Exemplo macOS/Linux: `/home/seu-usuario/projetos/Ticket`

## Ferramentas

Leitura: `mapa_site`, `listar_limites`, `analisar_preco`, `status_sessao`, `consultar_ofertas`.
Execução: `iniciar_sessao`, `encerrar_sessao`, `buscar_e_preparar_compra`, `liberar_pagamento`,
`obter_codigo_pix`, `finalizar_pagamento`.

Ver detalhes em `../../../docs/MCP-buyticket.md`.

## Smoke test

```powershell
.venv/Scripts/python _smoke.py
```
Deve imprimir a lista de tools e terminar com `SMOKE_OK`.
