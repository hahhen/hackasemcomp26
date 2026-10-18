# iFood Mesa

App de pedido em grupo: crie uma mesa, compartilhe o convite, converse sobre o
que cada pessoa quer e escolha pratos de um restaurante em comum. Backend em
FastAPI/LangGraph e frontend em React/TypeScript/Vite.

## Requisitos

- Python 3.11 ou superior.
- Node.js 22.12 ou superior e npm (Vite também aceita Node 20.19+).
- Para LLM remoto: chave do OpenCode Zen com créditos.
- Para LLM local: Ollama instalado e executando um modelo com suporte a ferramentas.
- Para dados reais: client ID/secret OAuth fornecidos pelo iFood e uma conta com endereço salvo.
- Para microfone: chave Gemini com acesso ao modelo de transcrição configurado.

## 1. Instalar

Execute no diretório do repositório. Os comandos abaixo usam PowerShell e o
Python do ambiente virtual diretamente, sem precisar ativar scripts:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
cd frontend
npm.cmd ci
cd ..
```

Se já existe um `.env`, preserve-o; copie o exemplo apenas na primeira instalação.
Em Linux/macOS, use `python3`, `.venv/bin/python`, `cp` e `npm` nos comandos equivalentes.

## 2. Configurar o `.env`

Edite o `.env` **na raiz do repositório**. O backend o carrega com precedência
sobre variáveis de mesmo nome no terminal. Reinicie o backend após alterar esse arquivo.

Há duas escolhas independentes:

| Configuração | Valor | Comportamento |
|---|---|---|
| `ENVIRONMENT` | `remote` | Texto pelo OpenCode Zen; usa a sua chave e créditos. |
| `ENVIRONMENT` | `local` | Texto pelo Ollama no seu computador. |
| `MESA_MODE` | `mock` | Catálogo, carrinho e pedidos simulados em Python. |
| `MESA_MODE` | `live` | Consultas e carrinho pelo MCP real do iFood. |

As quatro combinações funcionam. `mock` **não desliga** o LLM ou o Gemini:
ele substitui somente a integração iFood. True/God são modos de interface,
independentes dessas configurações.

### Texto remoto: OpenCode Zen

```dotenv
ENVIRONMENT=remote
OPENCODE_ZEN_API_KEY=sua-chave
OPENCODE_ZEN_MODEL=glm-5.3-flash
MESA_MODE=mock
```

Use `OPENCODE_ZEN_MODEL` para escolher outro modelo pago disponível na sua
conta Zen. Não há fallback automático para outros provedores. O nome padrão
preserva a configuração usada pelo projeto; disponibilidade e cobrança dependem
da sua conta.

### Texto local: Ollama

O preset existente se chama `ifood-agent` e usa Qwen no Ollama. Para recriá-lo:

```powershell
ollama pull qwen3:8b
ollama create ifood-agent -f Modelfile.ollama
```

Mantenha o Ollama executando (pelo aplicativo ou `ollama serve`) e configure:

```dotenv
ENVIRONMENT=local
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=ifood-agent
MESA_MODE=mock
```

Para usar um Llama ou outro modelo já instalado com suporte a ferramentas,
coloque seu nome exato em `OLLAMA_MODEL`. O `Modelfile.ollama` preserva o contexto
de 16.384 tokens do preset existente. Não é necessária uma chave Zen no modo local.

### Dados reais: MCP do iFood

```dotenv
MESA_MODE=live
MCP_CLIENT_ID=seu-client-id
MCP_CLIENT_SECRET=seu-client-secret
MCP_REDIRECT_URI=http://localhost:8080/agent
MESA_SUBMIT_ENABLED=false
```

O callback precisa corresponder exatamente ao registrado para o seu cliente.
Com a porta 8080 livre, execute na raiz:

```powershell
.\.venv\Scripts\python.exe -m agent.oauth
```

Conclua o login no navegador. O token fica em `.mcp-token.json` e pode ser
renovado automaticamente. Para refazer o login explicitamente, use
`python -m agent.oauth --force` com o Python da venv.

`MCP_BEARER_TOKEN`, se preenchido, substitui OAuth; um bearer estático não pode
ser renovado automaticamente. Deixe-o vazio no fluxo normal com client ID/secret.
O gateway carrega o catálogo completo de ferramentas MCP. A aplicação chama
essas ferramentas por Python; o LLM recebe apenas as ferramentas do Mesa.

Com `MESA_SUBMIT_ENABLED=false`, o envio final é simulado, **mas o carrinho live
ainda é criado e atualizado no iFood**. Use `MESA_MODE=mock` para simular toda a
integração. `MESA_SUBMIT_ENABLED=true` permite enviar um pedido real.

### Áudio: Gemini

```dotenv
GEMINI_API_KEY=sua-chave
GEMINI_AUDIO_MODEL=gemini-3.5-flash-lite
```

O exemplo preserva o modelo de áudio configurado nesta instalação. Ajuste o ID
para um modelo que aceite áudio e esteja disponível para sua chave. O microfone
envia a gravação ao backend, que pede apenas a transcrição ao Gemini; o texto
segue para o LLM local/remoto normalmente. Sem essa chave, o chat por texto
continua disponível. Microfone requer permissão do navegador e contexto seguro
(`localhost` ou HTTPS).

## 3. Executar

Terminal 1, na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent.mesa.api:app --reload --port 8000
```

Terminal 2, também partindo da raiz:

```powershell
cd frontend
npm.cmd run dev
```

Abra [http://localhost:5173](http://localhost:5173). O Vite encaminha `/api` para
o backend em `http://localhost:8000`. Se mudar a porta do backend, configure
`VITE_API_URL` em `frontend/.env.local` e reinicie o Vite:

```dotenv
VITE_API_URL=http://localhost:8001
```

Crie uma mesa e compartilhe seu código/link. Outras pessoas entram como membros;
para testar no mesmo computador, use outra janela/perfil do navegador. O modo
**God** permite inspecionar e enviar mensagens pelos participantes; **True**
mostra a experiência de um participante.

Para acesso por outros dispositivos na mesma rede, inicie ambos com
`--host 0.0.0.0` (`npm.cmd run dev -- --host 0.0.0.0`) e abra o IP do computador
na porta 5173. Compartilhar `localhost` não funciona em outro dispositivo.
Para usar o microfone fora de localhost, sirva a interface por HTTPS.

### Executar o frontend compilado junto do backend

```powershell
cd frontend
npm.cmd run build
cd ..
.\.venv\Scripts\python.exe -m uvicorn agent.mesa.api:app --port 8000
```

Abra [http://localhost:8000](http://localhost:8000). O backend serve `frontend/dist`
quando essa pasta existe ao iniciar. Refaça o build após alterar o frontend.
Use um único worker: salas, tarefas e locks são mantidos no processo Python.

## Estado, limites e diagnóstico

O chat mostra a atividade real do agente: entender o pedido, buscar restaurantes,
conferir pratos, adicionar ao carrinho, buscar cupons e preparar o pedido. Os
indicadores somem ao concluir, cancelar ou falhar. A interface consulta o estado
a cada 350 ms durante uma operação e a cada 1,5 s quando está ociosa.

As buscas começam assim que o agente registra uma mudança de desejo, em paralelo
à resposta final. Consultas iguais compartilham uma leitura dentro da mesma
busca; filtros, orçamento e paginação continuam separados. Lojas com oito sugestões
exatas por pessoa não precisam de uma busca extra para ampliar essas listas.
Resultados não são reaproveitados entre buscas e a validação do prato continua
acontecendo antes de adicioná-lo ao carrinho.

- `MESA_PERSIST=sessions.json` preserva mesas entre reinícios; vazio mantém só memória.
- `MESA_CHAT_LLM=true` usa o LLM na conversa. `MESA_RECOMMEND_LLM=false` mantém a
  extração de consultas por regras, sem uma chamada extra de modelo por busca.
- Para testar sem serviços externos: `MESA_MODE=mock`, `MESA_CHAT_LLM=false` e
  `MESA_RECOMMEND_LLM=false`; não use o microfone. O chat usa regras simplificadas.
- `MESA_CHAT_TIMEOUT`, `MESA_LLM_TIMEOUT`, `LLM_TIMEOUT` e `LLM_MAX_RETRIES`
  controlam os limites de espera; veja `.env.example`.
- `GET /api/health` mostra o modo e modelo configurados; não testa a disponibilidade do iFood.
- Logs estruturados ficam em `logs/observability.jsonl`; veja [OBSERVABILITY.md](OBSERVABILITY.md).
- Falhas `INTERNAL` do MCP não são resultados vazios. O gateway limita retentativas
  e pode tentar renovar OAuth e reconstruir o cliente após falhas repetidas de
  leitura (`listAddresses`, `getItem`, `getMerchant`), inclusive em mesas com
  endereço salvo. Essa recuperação tem intervalo mínimo de cinco minutos. Use os logs
  estruturados para identificar a ferramenta e o código de erro.

```powershell
.\.venv\Scripts\python.exe scripts/observability_report.py
# Diagnóstico live somente leitura, usando token salvo válido:
.\.venv\Scripts\python.exe scripts/probe_mcp_http.py
```

## Verificar alterações

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd run build
```

Os testes usam iFood simulado e respostas de modelo/transcrição controladas;
não gastam créditos nem fazem pedidos reais. Testes locais não garantem a
disponibilidade de serviços externos.

## Estrutura

- `src/agent/mesa/`: API, chat, recomendações, carrinho, checkout e observabilidade.
- `src/agent/providers.py`: Ollama/Zen; `oauth.py` e `mcp.py`: integração MCP.
- `frontend/src/`: interface True/God, salas, sugestões e carrinho compartilhado.
- `tests/`: testes automatizados; `scripts/`: inventário e diagnósticos explícitos.
- [ifood-mcp-tools.json](ifood-mcp-tools.json): inventário de schemas do MCP.

As confirmações de Pix são simuladas e a identidade é local ao navegador. O
protótipo usa a conta iFood autenticada na instalação do backend.
