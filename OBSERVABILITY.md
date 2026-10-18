# Observabilidade do iFood Mesa

Ativa por padrão, sem serviço externo ou chave. Reinicie o backend para carregar a
instrumentação. Os eventos JSONL ficam em `logs/observability.jsonl`, com rotação
de 5 MB e três backups. Para configurar:

```dotenv
MESA_OBSERVABILITY=true
MESA_TRACE_FILE=logs/observability.jsonl
```

Cada execução de busca, chat, fechamento do carrinho, checkout, adição ao carrinho
e rastreio recebe um `run_id`. O `order_id` é um identificador local persistido na
mesa: permite correlacionar buscas anteriores à criação de um pedido real. Uma
mesa nova recebe outro identificador. Não é o identificador de pedido do iFood.

Eventos `node.start/end` registram nó, duração em milissegundos e status (`ok`,
`error`, `cancelled`) nos grafos de recomendações e checkout. Resultados contendo
`error` também marcam falha, mesmo quando o nó não lança uma exceção.

Eventos `tool.call.start/end` medem chamadas lógicas no gateway (incluindo retry).
`tool.attempt.start/end` medem cada invocação ao adaptador MCP no gateway live,
inclusive a nova tentativa após renovar autenticação. Assim, uma chamada que
falha duas vezes e funciona na terceira gera uma chamada e três tentativas.
Mock gera chamadas, sem tentativas de rede.

As leituras `listAddresses`, `getItem` e `getMerchant` podem renovar OAuth e
reconstruir o cliente antes da terceira tentativa, após dois erros `INTERNAL`.
O limite é compartilhado entre ferramentas e salas: uma recuperação a cada
cinco minutos. Isso também cobre mesas que já têm endereço salvo e não chamam
`listAddresses`. Não se aplica a bearer estático ou a mutações com erro interno;
falha persistente continua sendo reportada.

O diagnóstico HTTP permite comparar o item publicado em uma mesa com o catálogo
atual sem criar carrinho. `--compare-refresh` renova explicitamente o login salvo
e compara os tokens em memória, sem escrevê-los no relatório:

```powershell
.\.venv\Scripts\python.exe scripts/probe_mcp_http.py --room CODIGO --output artifacts/mcp-item-diagnostic.json
.\.venv\Scripts\python.exe scripts/probe_mcp_http.py --room CODIGO --compare-refresh --output artifacts/mcp-item-refresh.json
```

Na reprodução de 18/10 às 12:19 UTC, o item salvo, endereço, perfil e busca
falharam por HTTP direto com `INTERNAL`. Às 12:20 UTC, o mesmo item voltou a
responder antes da renovação explícita, e respondeu também depois dela.
Portanto, esse episódio não comprova que renovação seja a causa da recuperação.
Os relatórios em `artifacts/mcp-item-diagnostic-20261018.json` e
`artifacts/mcp-item-refresh-comparison-20261018.json` preservam horários e IDs
das chamadas, sem coordenadas ou credenciais.

Campos comuns: `timestamp` UTC, `room_id`, `order_id`, `run_id`, `graph`, `stage`.
Falhas incluem `error_type`, `error_category` e `mcp_tool`.
As falhas do gateway também preservam `mcp_code` (por exemplo, `INTERNAL` ou
`UNAUTHENTICATED`) mesmo depois de esgotar retentativas. A duração inclui espera
no limite global de concorrência, reconexão do adaptador e retentativas no evento
de chamada lógica; não representa exclusivamente o tempo de processamento do iFood.
Mensagens brutas de erro, argumentos, resultados, áudio, prompts, endereços e credenciais não são
gravados. A classificação é heurística; `unknown` requer diagnóstico adicional.
Chamadas diretas fora de uma operação recebem correlação nula (`unscoped`).

`run.end` contém contadores por `etapa/ferramenta`: `tool_calls`, `tool_attempts`
e `tool_failures`. Falhas recuperadas continuam visíveis nas tentativas; uma
operação que aproveitou resultados parciais pode terminar com status `ok`.

## Relatório para o feedback

```powershell
.\.venv\Scripts\python.exe scripts/observability_report.py
.\.venv\Scripts\python.exe scripts/observability_report.py --order-id ID_LOCAL
```

O relatório Markdown inclui volume, falhas, cancelamentos, média/p95 por tool,
categorias de MCPError, chamadas por pedido e etapa e execuções por status.
Lê o arquivo atual e backups rotacionados. Os números cobrem somente os logs
ainda retidos. Redirecione a saída para um arquivo e anexe ao MCP-FEEDBACK.md
quando documentar um incidente; o documento de feedback não é alterado durante
pedidos. Nenhum dado é enviado automaticamente ao iFood.
