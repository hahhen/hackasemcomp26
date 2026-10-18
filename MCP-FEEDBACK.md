# Feedback — MCP do iFood (`sudowin-semcomp`)

**Data:** 17–18/10/2026
**Endpoint:** `https://consumer-mcp.ifood.com.br/marketplace`
**Cliente testado:** agente de pedidos em Python 3.14 (LangGraph/ReAct) usando `langchain-mcp`,
com `fastmcp` 4.1.0 e `mcp` 2.3.0
**Objetivo:** construir um agente que descobre restaurantes, monta carrinho e fecha pedido
usando as 23 ferramentas expostas pelo MCP

Este documento lista **dificuldades e dores reais** sentidas durante a integração, com
evidências reproduzíveis, para servir de insumo direto ao time que mantém a ferramenta.

---

## TL;DR

O servidor acerta em pontos importantes (adota a RFC 9728, responde `401` com
`WWW-Authenticate`, usa PKCE S256 e emite `refresh_token`). O que travou o
desenvolvimento foi outra coisa:

| # | Dor | Severidade | Custo real |
|---|-----|-----------|------------|
| 1 | Esquema das 23 ferramentas = **10.508 tokens**, enviados em **toda** chamada | **Bloqueador** | `HTTP 413` em todo tier gratuito na nuvem; só o servidor **completo** rodou em modelo local |
| 2 | OAuth incompatível com bibliotecas de cliente prontas (callback fixo) | **Bloqueador** | OAuth 2.1 + PKCE teve de ser implementado à mão |
| 3 | Descoberta OIDC inconsistente (issuer `…/3p` vs. metadata na raiz) | **Bloqueador** | Endpoints tiveram de ser fixados no código, ignorando a descoberta |
| 4 | Contrato das ferramentas em **prosa**, não no schema | Alto | Agente erra o passo a passo; falha silenciosa |
| 5 | Nenhuma ferramenta declara `outputSchema` (**0 de 23**) | Alto | Consumidor é obrigado a fazer parsing de texto livre |
| 6 | `annotations` inconsistentes e até contraditórias | Médio | Impossível montar política de confirmação confiável |
| 7 | `scopes_supported` ausente nas duas metadata | Médio | Os 17 scopes só são descobertos lendo a resposta do token |
| 8 | Não há *device code flow* | Médio | Impossível autenticar em CI/ambiente sem navegador |
| 9 | `redirect_uri` exige `localhost:8080` byte a byte | Médio | Basta outro processo na 8080 para o login falhar |
| 10 | `state` de aba antiga derruba o fluxo em andamento | Baixo | Erro confuso, parece credencial inválida |
| 11 | Credenciais OAuth exigem substituição recorrente (`invalid_client`); causa do refresh não confirmada | **Bloqueador** | Interrupções para obter novos secrets e autenticar novamente |
| 12 | Falhas `INTERNAL`, respostas incompletas e escrita sem confirmação | **Bloqueador** | Endereço, busca ou carrinho ficam indisponíveis mesmo após autenticação bem-sucedida |

---

## 1. Esquema das ferramentas é grande demais para qualquer tier gratuito
### 🔴 Bloqueador — o mais caro de todos

### Evidência

Medição do `tools/list`, serializado como schema OpenAI e contado com o tokenizer
`o200k_base`:

```
TOTAL 23 ferramentas = 10.508 tokens
    1329  searchOffers
    1300  createCart
    1279  browseOffers
    1125  getRecommendations
     722  createAddress
     703  updateCartItem
     694  addItemToCart
     ...
     104  getOrder
```

> O total varia entre ~10.5k e ~10.7k conforme o cliente serializa o schema. A
> medição ferramenta por ferramenta está em `IFOOD-MCP-TOOLS.md`, e os schemas brutos
> em `ifood-mcp-tools.json`.

Descrições mais longas em **caracteres** (uma única descrição!):

```
    1717  createAddress
    1392  listAddresses
     739  browseOffers
```

Esse schema é reenviado em **toda** chamada de modelo. Ao apontar o agente para o
tier gratuito do Groq:

```
HTTP 413 - Request too large for model `openai/gpt-oss-120b`
on tokens per minute (TPM): Limit 8000, Requested 9005
```

O `Requested` variou entre **9.005 e 9.390 tokens** conforme o conteúdo — sempre acima
do limite de 8.000. Ou seja: **com as 23 ferramentas, nenhuma requisição chega a ser
processada.** O erro acontece antes da conversa começar, porque o schema sozinho já
estoura o teto.

Mesmo em tiers pagos o custo é relevante: ~10,7k tokens de entrada em **cada** turno
do agente, para uma pergunta que talvez use 1 ou 2 ferramentas.

**Relato de uso:** esse custo repetido tornou a integração muito cara. Com o catálogo
completo vinculado ao modelo, cada chamada ao LLM carrega cerca de **10 mil tokens
só de schemas**, antes do histórico, da mensagem do usuário e dos resultados das
ferramentas. Um fluxo com cinco chamadas ao modelo acumula aproximadamente **52,5 mil
tokens de entrada só de schemas**. O valor cobrado depende do provedor e de eventuais
descontos de cache, mas o overhead se repete a cada etapa do agente.

Precisão: esse overhead está nas **requisições ao LLM que incluem as ferramentas do
MCP**; não significa que cada chamada HTTP ao servidor MCP consuma tokens de modelo.

**Escopo da medição:** os ~10,5k tokens correspondem ao agente que vincula diretamente
as 23 ferramentas brutas. O chat atual do **iFood Mesa** usa seis ferramentas locais
mais curtas; o backend chama o MCP em Python para buscar e montar os pedidos. Nesse
fluxo, o schema completo do MCP não é enviado ao LLM em cada inferência. Portanto,
logs menores de tokens são compatíveis com a arquitetura atual. Chamadas ao MCP e
chamadas ao provedor de LLM são operações distintas.

### Consequências observadas em outros provedores

O mesmo schema grande se manifesta de formas **diferentes e igualmente confusas** em cada
backend — o que torna a causa raiz difícil de enxergar:

**Groq (tier gratuito, 8k TPM)** — `HTTP 413 Request too large`. Falha imediata e clara.

**Gemini 2.5 Flash (tier gratuito = 20 requisições por dia)** — com o payload completo
(25 ferramentas, 10.138 tokens de prompt), passou a ocorrer de forma intermitente uma
resposta **`HTTP 200` com conteúdo vazio** (`finish_reason="stop"`,
`completion_tokens=0`). O agente ReAct então **encerrava com resposta em branco** — sem
exceção, sem log, sem erro.

**Ollama (modelo local)** — o Ollama usa janela de contexto de **4096 tokens por
padrão**. Com o schema de ~10,5k tokens, a lista de ferramentas é **truncada em
silêncio**: nenhuma mensagem de erro, e o modelo simplesmente responde que **não tem
aquelas ferramentas**. Só é possível rodar o servidor completo declarando
`num_ctx=16384` explicitamente — e esse parâmetro **não é aceito pela API compatível com
OpenAI**, exigindo criar um modelo derivado (Modelfile) antes de qualquer coisa.

Em todos os três casos a causa é a mesma — o schema não cabe no orçamento de contexto do
cliente — mas **nenhum dos três sintomas aponta para ela**. Sem tratar isso, o usuário
final vê "o agente não respondeu", "a ferramenta não existe" ou um erro de limite de
plano, e não o problema real.

### Impacto

- **Impede o uso do servidor completo em qualquer tier gratuito.**
- Força o cliente a escolher um subconjunto de ferramentas, perdendo capacidades.
- Aumenta latência e custo em qualquer tier.

### O que tivemos de fazer

Filtrar ferramentas no cliente. Medições de subconjuntos, para referência:

| Subconjunto | Ferramentas | Tokens de schema |
|---|---|---|
| Todas | 23 | **10.508** |
| Sem as 4 mais pesadas | 19 | 7.258 |
| Fluxo de pedido completo | 12 | 6.207 |
| Fluxo mínimo | 9 | 4.999 |

O melhor resultado veio com **12 ferramentas (6.207 tokens)**, que passa a caber no
tier gratuito — mas significa abrir mão de `browseOffers`, `getRecommendations`,
`createAddress` e de todo o fluxo de vouchers.

A única forma **gratuita** que encontramos de usar o servidor **completo** foi rodar um
modelo **local** (Ollama + `qwen3:8b`, 8 GB de VRAM): não existe limite de tokens por
minuto, então os 10,5k tokens do schema deixam de ser problema. Ou seja: **hoje o MCP
completo só é acessível a quem tem hardware local.** Esse é um argumento forte para
enxugar o schema — e não para exigir mais do cliente.

### Sugestão

1. **Descrições curtas (1–2 frases) + `description` por campo do schema.** A meta
   razoável é **< 150 tokens por ferramenta**; hoje `searchOffers` sozinha custa 1.329.
2. **Tirar o "passo a passo" do schema.** Instruções como
   *"**Two-step flow (default):** call with `address` only → returns `matchConfidence`
   … → call again with the chosen `candidateId`"* são **orquestração**, não documentação
   de API. Devem voltar na **resposta** da ferramenta (ex.: um campo `nextSteps`) ou
   virar uma ferramenta de ajuda separada (`getUsageGuide`), carregada só quando o
   agente precisa.
3. **Considerar dividir em sub-servidores** (ex.: `catalog`, `cart`, `orders`) para que
   o cliente possa carregar só o namespace que interessa. Hoje não há como pedir
   "só as ferramentas de carrinho" via `tools/list`.
4. **Publicar o número de tokens por ferramenta na documentação**, para o time
   dimensionar o orçamento de contexto antes de integrar.

---

## 2. OAuth não funciona com bibliotecas de cliente prontas
### 🔴 Bloqueador

### Evidência

- O `redirect_uri` registrado para este cliente é **exatamente**
  `http://localhost:8080/agent` (contrato do `setup-sudowin-mcp.mjs`).
- O iFood valida a `redirect_uri` **byte a byte**: não aceita `127.0.0.1` no lugar de
  `localhost`, nem porta diferente, nem barra extra.
- As bibliotecas de cliente MCP **assumem um caminho de callback fixo** — o FastMCP usa
  `/callback`. É impossível configurá-lo para `/agent`.

Resultado: o suporte a OAuth embutido no framework é **inutilizável** com este servidor.

### Impacto

Foi necessário implementar **manualmente** todo o fluxo authorization code + PKCE:
montagem da URL de autorização, `code_verifier`/`code_challenge` S256, servidor HTTP de
callback, troca do code por token com `client_secret_basic`, cache em disco, refresh e
tratamento de expiração. É código não trivial, específico da integração, replicado por
todo cliente que tente usar este MCP.

### Sugestão

1. **Registrar uma segunda `redirect_uri`** (`…/callback`) no cliente, permitindo usar o
   OAuth nativo dos frameworks.
2. Ou **tornar o caminho de callback configurável**, e documentar o valor exigido.
3. Publicar um **exemplo de código completo e testado** (authorization code + PKCE +
   client_secret_basic + refresh). É o melhor investimento para reduzir o atrito,
   porque hoje cada time reinventa esse fluxo — e erra.

---

## 3. Descoberta OIDC inconsistente
### 🔴 Bloqueador

### Evidência

Metadata do recurso protegido (RFC 9728):
`GET https://consumer-mcp.ifood.com.br/.well-known/oauth-protected-resource/marketplace`

```json
{"resource":"https://consumer-mcp.ifood.com.br/marketplace",
 "authorization_servers":["https://id.ifood.com.br"]}
```

Se um cliente seguir o padrão, vai buscar a metadata em `https://id.ifood.com.br`:

`GET https://id.ifood.com.br/.well-known/openid-configuration`

```json
{"issuer":"https://id.ifood.com.br/3p",
 "authorization_endpoint":"https://id.ifood.com.br/authorize",
 "token_endpoint":"https://id.ifood.com.br/token", ...}
```

Dois problemas:

1. **O `issuer` não corresponde à URL de onde o documento foi baixado.** Pela
   RFC 8414 / OIDC Discovery, o `issuer` **deve** ser igual à URL usada na consulta,
   removido o sufixo `/.well-known/…`. Aqui `https://id.ifood.com.br` ≠
   `https://id.ifood.com.br/3p`. Clientes que validam isso **rejeitam a metadata**.
2. **Não existe documento na URL do issuer.** A consulta a
   `https://id.ifood.com.br/3p/.well-known/openid-configuration` retorna **HTTP 400**.
   Ou seja, não há caminho de descoberta que feche.

### Impacto

A descoberta automática não funciona: foi preciso **fixar** os endpoints
(`authorize`, `token`) direto no código. Libs genéricas de OAuth falham sem mensagem
clara, e a impressão inicial é de que a URL de metadata está errada.

### Sugestão

- Fazer `authorization_servers` apontar para **`https://id.ifood.com.br/3p`**, **e**
  servir o documento em `https://id.ifood.com.br/3p/.well-known/openid-configuration`
  com `issuer` idêntico. Assim a descoberta passa a ser válida de ponta a ponta.
- Alternativa mínima: servir o mesmo documento **também** na raiz, mas com
  `"issuer": "https://id.ifood.com.br"`.

---

## 4. O contrato das ferramentas está em prosa, não no schema
### 🟠 Alto

### Evidência

`createAddress` tem **1.717 caracteres** de descrição, incluindo o fluxo de dois passos
e as regras de quando usar o atalho de passo único. O mesmo vale para `listAddresses`
(1.392 caracteres).

Três evidências concretas, todas verificáveis no schema enviado ao modelo:

1. **`alias` deveria ser `enum` e não é.** A descrição diz *"exactly **CASA** or
   **TRABALHO**"*, mas o schema declara `"type": "string"`, sem `enum`. O servidor
   **sabe** fazer isso em outro lugar: `searchOffers.refinements.sort` declara
   `enum: [RELEVANCE, DELIVERY_FEE, DELIVERY_TIME, DISTANCE, PRICE]`. É inconsistente
   dentro do mesmo servidor.
2. **`createAddress` não tem nenhum campo obrigatório:** `"required": []`. Mas a prosa
   diz que `address` é *"Required on geocode and confirmation calls"*. A
   obrigatoriedade existe **só no texto** — nenhum cliente ou validador consegue
   aplicá-la.
3. **Campos opcionais com "default" falso.** Vários campos vêm com
   `"default": "##default"` — um marcador literal do servidor, não um valor. Um modelo
   pode perfeitamente enviar a string `"##default"` como argumento, porque o schema
   afirma que esse é o default.

Exemplos de trechos que são instrução de prompt, não documentação de API:

> *"**When to use:** after `listAddresses` returned empty or the user chose to add a new
> address."*
> *"**Single-shot shortcut:** only when `matchConfidence` is `SINGLE` and the user already
> provided `address` + `complement` in the same interaction with a resolvable
> neighborhood, send both together to create directly."*

### Impacto

O agente precisa **ler um parágrafo e obedecer**. Modelos erram esse tipo de regra com
frequência, e o erro é **silencioso**: a chamada "funciona", só produz o resultado
errado (ex.: pular a confirmação de candidato). Além disso, o texto todo é pago em
tokens em cada chamada (ver item 1).

### Sugestão

- Modelar o contrato **no schema**: `matchConfidence` como `enum: [SINGLE, MULTIPLE]`.
- Preferir **duas ferramentas explícitas** (`resolveAddress` → `createAddress`) em vez de
  um fluxo condicional descrito em prosa.
- Manter na descrição apenas o que é factual: o que faz, o que retorna, o que exige.

---

## 5. Nenhuma ferramenta declara `outputSchema`
### 🟠 Alto

### Evidência

Inspeção do `tools/list` no cliente:

```
tool count: 23
tools declaring outputSchema: 0
```

Todas retornam texto livre. Não há `structuredContent`, nem contrato de saída.

### Impacto

O consumidor precisa **parsear prosa** para extrair `addressId`, `merchantId`,
`cartId`, preços etc. Isso é frágil: qualquer ajuste de redação quebra o cliente.
Para um agente, é pior ainda — o modelo recebe texto e pode "alucinar" um campo. Em
`listAddresses` isso funcionou porque extraímos o endereço direto do texto, mas não há
garantia nenhuma de formato.

### Sugestão

Declarar `outputSchema` e devolver `structuredContent` nas ferramentas que produzem
identificadores e valores (`listAddresses`, `createCart`, `searchOffers`, `getOrder`…).
Isso torna a integração determinística e reduz o volume de texto trafegado.

---

## 6. `annotations` inconsistentes e contraditórias
### 🟡 Médio

### Evidência

Distribuição das anotações nas 23 ferramentas:

```
combo (readOnly=False, destructive=True,  idempotent=False, openWorld=True) -> 16 ferramentas
combo (readOnly=True,  destructive=True,  idempotent=False, openWorld=True) ->  5 ferramentas
combo (readOnly=True,  destructive=False, idempotent=True,  openWorld=True) ->  2 ferramentas
```

Dois problemas concretos:

1. **Contradição:** `browseOffers`, `getItem`, `getMerchant`, `getRecommendations` e
   `searchOffers` estão marcadas **simultaneamente** como `read_only_hint=True` e
   `destructive_hint=True`. Uma ferramenta somente-leitura não pode ser destrutiva.
2. **Leituras marcadas como destrutivas:** `getCart`, `getCartDeliveryMethods`,
   `listPaymentMethods`, `listVouchers`, `getOrder` e `trackOrder` estão com
   `read_only_hint=False, destructive_hint=True` — o servidor está dizendo ao cliente
   que **consultar** pode destruir dados.

E 16 das 23 ficaram no combo padrão, que é exatamente o valor "não informado" da spec
(`read_only=False, destructive=True`). Na prática, para a maioria das ferramentas a
anotação não comunica nada.

### Impacto

O cliente MCP não consegue montar uma política confiável de confirmação/auto-execução:
- quem age por `destructive_hint` vai **pedir confirmação para listar o carrinho**;
- quem age por `read_only_hint` não distingue nada em 16 ferramentas.

Num agente isso vira ruído de confirmação (pior UX) ou, no sentido oposto, execução sem
supervisão do que deveria ser confirmado.

### Sugestão

- Consistência: `read_only_hint=True` ⟹ `destructive_hint=False`.
- Marcar corretamente as leituras (`getCart`, `getOrder`, `trackOrder`,
  `listVouchers`, `listPaymentMethods`, `getCartDeliveryMethods`) como somente-leitura e
  idempotentes.
- Marcar como destrutivas apenas as escritas que realmente são
  (`submitOrder`, `removeItemFromCart`, `removeVoucher`, `setCartPayment`,
  `updateCartItem`, `addItemToCart`).

---

## 7. `scopes_supported` ausente nas duas metadata
### 🟡 Médio

### Evidência

- A metadata do recurso protegido traz apenas `resource` e `authorization_servers` —
  **sem `scopes_supported`**.
- A metadata do authorization server lista `response_types_supported`,
  `grant_types_supported`, `token_endpoint_auth_methods_supported`,
  `code_challenge_methods_supported` e `revocation_endpoint` — mas **também sem
  `scopes_supported`**.

Os scopes só aparecem **depois** de completar o login, na resposta do token:

```
scope = "mcp.user mcp.cart mcp.discovery mcp.user.basic.read mcp.user.advanced.read
         mcp.user.addresses.read mcp.user.behavior.read mcp.user.addresses.write
         mcp.discovery.search.read mcp.discovery.browse.read
         mcp.discovery.recommendation.read mcp.discovery.details.read mcp.cart.read
         mcp.cart.write mcp.cart.checkout mcp.cart.payment mcp.orders.read
         capability.only-test-agent"
```

São **17 scopes**, concedidos integralmente sem que houvesse pedido explícito de escopo.

### Impacto

- O desenvolvedor não sabe o que pedir, nem o que é necessário. Enviamos **nenhum**
  scope e recebemos todos — funciona, mas por sorte, não por leitura da documentação.
- Não há como pedir acesso mínimo (princípio do menor privilégio), porque não se sabe
  os nomes antes de autenticar.
- A nomenclatura fica visível apenas tardiamente: `capability.only-test-agent` é
  particularmente enigmático para quem está integrando.

### Sugestão

Publicar `scopes_supported` nas duas metadata e documentar quais são obrigatórios para
qual conjunto de ferramentas. Seria valioso permitir pedir **menos** escopos (ex.: só
`mcp.discovery.*` + `mcp.orders.read`) para um agente de consulta.

---

## 8. Não existe *device code flow*
### 🟡 Médio

### Evidência

`grant_types_supported: ["authorization_code", "refresh_token"]`.

O único caminho de autenticação é **authorization code com navegador**, em
`localhost:8080`. Não há *device authorization grant* (RFC 8628).

### Impacto

- **Impossível autenticar em CI, servidor remoto ou container** — exatamente o ambiente
  onde um agente costuma rodar.
- Impossível autenticar em máquina sem interface gráfica.
- Cada desenvolvedor precisa de navegador com acesso a `localhost:8080` na mesma máquina.

### Sugestão

Adicionar `device_code` (RFC 8628). É o que resolve o caso de uso de agentes e CLIs
sem interação; hoje é o principal impedimento para automação.

### Nota positiva

O `refresh_token` **é emitido** e o access token dura **2 horas** (`expires_in: 7199`),
o que é adequado e permitiu implementar renovação automática. O ponto é que a
**primeira** autenticação exige navegador.

---

## 9. `redirect_uri` exige a porta 8080 byte a byte
### 🟡 Médio

### Evidência

O contrato exige `http://localhost:8080/agent` — nem `127.0.0.1`, nem `:8081`, nem
barra final. Se qualquer outro processo estiver ocupando a porta 8080, o navegador não
consegue entregar o callback e **o login falha sem dizer por quê**.

### Impacto

Conflito silencioso com qualquer serviço local na 8080. O sintoma é "o navegador
redirecionou mas nada aconteceu", o que leva a debugar credenciais quando o problema é
de rede/porta.

### Sugestão

- Documentar a exigência da porta **de forma destacada**, junto do aviso de que a 8080
  precisa estar livre durante o login.
- Idealmente, permitir **múltiplas `redirect_uri` registradas** (inclusive com portas
  alternativas), como fazem os provedores OAuth consolidados.
- Melhorar a mensagem quando o callback não chega: hoje o cliente fica "esperando para
  sempre".

---

## 10. `state` de aba antiga derruba o fluxo em andamento
### 🟢 Baixo

### Evidência

Cada tentativa de autorização abre uma aba com um `state` novo. Se o desenvolvedor
completa o login numa aba **aberta antes de uma reinicialização**, o `state` que chega é
o antigo, não corresponde ao esperado, e:

- numa primeira versão do nosso cliente, **abortava o fluxo inteiro** com mismatch,
  embora as credenciais estivessem corretas;
- o erro exibido é genérico e não sugere "use a aba mais recente".

### Impacto

Falhas confusas durante o desenvolvimento, que aparentam credencial inválida. O
comportamento depende do cliente, mas o desenho do fluxo (abrir aba automaticamente a
cada execução + `state` de uso único) torna o cenário fácil de acontecer.

### Sugestão

- Documentar que o `state` é de **uso único** e que abas antigas devem ser fechadas.
- Deixar a mensagem de erro explícita nesse caso ("este link pertence a uma tentativa
  anterior; volte ao terminal e abra a URL atual").
- Considerar um comportamento de retentativa, em vez de erro, quando o `state` for
  claramente de uma tentativa anterior do mesmo cliente.

---

## 11. Credenciais OAuth param de funcionar e exigem substituição recorrente
### 🔴 Bloqueador

### Evidência

O login original (authorization code) funcionou e emitiu um token válido por 2h.
~2h depois, ao tentar renovar, o token endpoint respondeu `invalid_client`. Ao
tentar um novo login, o **authorize endpoint** respondeu:

```
GET https://id.ifood.com.br/authorize?response_type=code&client_id=3q0Cz-...&...

HTTP 401
{"error":"invalid_client",
 "error_description":"Client authentication failed (e.g., unknown client, no
 client authentication included, or unsupported authentication method).
 The requested OAuth 2.0 Client does not exist."}
```

A frase final — **"The requested OAuth 2.0 Client does not exist"** — indica que o
servidor não reconheceu o client apresentado. Isso, isoladamente, **não comprova
revogação/exclusão**, nem determina a causa da falha de renovação.

**Relato do desenvolvedor:** foi necessário obter credenciais diferentes (client
ID/secret) de tempos em tempos para continuar usando a integração, porque a renovação
aparentava não funcionar. A relação com uma falha de refresh é uma **hipótese do
desenvolvedor, ainda não confirmada**. É necessário esclarecer com o provedor se há
expiração ou rotação dos clients, revogação, ou outro problema de autenticação.

**Esclarecimento do relato:** a substituição necessária era do **par client ID +
client secret**, e não apenas do secret. Trocar somente o secret mantendo o mesmo
client ID continuou produzindo `invalid_client` com a mensagem "The requested OAuth
2.0 Client does not exist". Isso não confirma, por si só, a causa da falha de refresh.

### Impacto

- **Integração inteiramente bloqueada.** Sem um client válido, não há como
  autenticar de novo; o agente fica sem acesso ao MCP.
- A mensagem não permite distinguir com segurança credenciais incorretas, client
  expirado/revogado ou outra falha de configuração; o diagnóstico fica incerto.
- A necessidade recorrente de obter novos secrets interrompe o desenvolvimento e
  exige intervenção manual, em vez de uma renovação transparente da sessão.
- Para um hackathon, é crítico: as credenciais distribuídas no
  `setup-sudowin-mcp.mjs` deixaram de funcionar no meio do evento, sem aviso.

### Sugestão

- **Não revogar clients em uso** durante o evento, ou avisar com antecedência.
- Documentar a validade de client IDs/secrets e refresh tokens, a política de rotação
  e o procedimento de recuperação; investigar por que a troca recorrente foi necessária.
- Se a revogação for intencional (rotação), **publicar novos client_id/secret** e
  um canal de aviso.
- Distinguir os erros: `invalid_client` para credencial errada, e um erro próprio
  (ex.: `client_revoked`) para client revogado — hoje os dois são indistinguíveis.

---

## 12. Falhas operacionais intermitentes nas ferramentas de endereço, busca e carrinho

**Coleta de evidências:** a instrumentação em [OBSERVABILITY.md](OBSERVABILITY.md)
registra nó, latência, status, MCPError e contagens por etapa/pedido. Gere um
anexo com `python scripts/observability_report.py --order-id ID_LOCAL` para
quantificar novos incidentes. Nenhuma chamada ao MCP é feita pelo relatório.
### 🔴 Bloqueador para a experiência de pedido

Além dos problemas de contrato e OAuth acima, a integração apresentou falhas de execução
intermitentes depois de uma autenticação bem-sucedida. Estes casos são separados de uma
lista vazia legítima: uma lista vazia pode significar que não há resultados; uma resposta
incompleta ou um erro `INTERNAL` não pode ser interpretado como ausência de endereço,
restaurante ou item.

### Evidências observadas

#### `listAddresses`: MCP acessível, mas endereço indisponível

Em 18/10, a descoberta de ferramentas funcionou normalmente e retornou **23 tools** para
o client autenticado. Logo depois, uma chamada de leitura sem parâmetros a
`listAddresses` falhou três vezes consecutivas com:

```text
code: "INTERNAL"
message: "An internal failure at an upstream service prevented the request from completing."
```

O mesmo resultado foi reproduzido fora da interface da aplicação, usando uma chamada
direta e somente-leitura ao MCP. Isso descarta o LLM, filtros de busca e a UI como causa
direta do incidente. O efeito é grave: sem `addressId` e coordenadas, não é possível
iniciar `searchOffers` nem criar carrinho.

O client passou a distinguir explicitamente este caso de uma lista de endereços vazia e
a tentar a leitura até três vezes. Mesmo assim, a operação continuou indisponível.

#### `searchOffers` / `browseOffers`: erros internos e respostas incompletas

Durante buscas de pratos e restaurantes ocorreram, em momentos distintos:

```text
An internal failure at an upstream service prevented the request from completing.
Resposta sem lista de resultados; nao e uma busca vazia.
A busca retornou uma resposta incompleta apos 3 tentativas.
```

O segundo caso é especialmente problemático: a chamada não traz a lista `results`, mas
também não representa uma busca vazia. Sem `outputSchema`, o consumidor precisa inferir
se uma resposta é um erro, uma envelope inesperada ou um resultado válido. Isso interrompe
a reconciliação de uma mesa quando uma pessoa nova entra ou altera seu desejo, mesmo que
as sugestões anteriores tenham funcionado.

Também foram observadas respostas parciais ao tentar ampliar cardápios por restaurante.
O client teve de preservar os pratos já encontrados em vez de descartar toda a busca.

#### `getItem`: detalhes de cardápio lentos ou indisponíveis em volume

Para duas pessoas e dez restaurantes, a busca retornou centenas de pratos e o fluxo de
detalhes chegou a registrar:

```text
Conferindo detalhes de 298 pratos em 10 restaurantes.
```

A etapa permaneceu sem concluir por mais de dez minutos. Parte desse tempo decorre de
uma estratégia defensiva do cliente que pedia detalhes de muitos pratos, mas a ferramenta
não ofereceu garantias de latência nem uma forma de pedir detalhes em lote. O client teve
de limitar sugestões e impor timeout por detalhe para não bloquear a mesa inteira.

#### `createCart`: sucesso sem identificador e falha upstream em operação mutável

Ao fechar o carrinho, foram recebidas respostas de `createCart` sem `cartId`, e em outro
momento:

```text
An internal failure at an upstream service prevented the request from completing.
```

Para uma operação mutável isso é ambíguo: o servidor pode ter criado o carrinho antes de
falhar a resposta. Repetir automaticamente arrisca duplicar o carrinho; não repetir deixa
o usuário sem saber se deve tentar de novo. O client optou por não repetir e comunicar a
incerteza, mas o protocolo deveria oferecer confirmação idempotente.

### Impacto

- Uma autenticação e `tools/list` bem-sucedidos não garantem que o fluxo de pedido possa
  começar ou terminar.
- Falhas de infraestrutura chegam misturadas a respostas sem contrato, dificultando
  retentativa correta e diagnóstico pelo usuário.
- Operações de escrita ficam sem semântica segura de retry quando a resposta não confirma
  o identificador criado.

### Sugestões

1. Corrigir ou isolar a dependência upstream de `listAddresses`; expor status/erro
   específico em vez de apenas `INTERNAL`.
2. Declarar `outputSchema` obrigatório com `results`, `cartId`, códigos de erro e campo
   de retentativa (`retryable`) para todas as ferramentas críticas.
3. Garantir que `searchOffers`/`browseOffers` sempre retornem uma lista válida, inclusive
   quando vazia, ou um erro estruturado inequívoco.
4. Tornar `createCart` idempotente com uma chave do cliente, ou expor uma consulta para
   localizar com segurança o carrinho criado após uma falha de transporte.
5. Documentar limites de latência, paginação e throughput de `getItem`; considerar um
   endpoint de detalhes em lote para interfaces que precisam mostrar várias sugestões.

---

## O que funcionou bem

Vale registrar, porque nem tudo é crítica:

- **RFC 9728 de verdade.** Um `POST` não autenticado devolve `401` com
  `WWW-Authenticate: Bearer error="invalid_token", resource_metadata="https://consumer-mcp.ifood.com.br/.well-known/oauth-protected-resource/marketplace"`.
  Isso permitiu descobrir todo o fluxo de auth **a partir do erro**, o que é exatamente
  o comportamento desejado e raro na prática.
- **PKCE S256** anunciado e exigido corretamente
  (`code_challenge_methods_supported: ["S256"]`).
- **`refresh_token` + expiração de 2h**, adequados para renovação automática.
- **23 ferramentas com nomes claros**, cobrindo o fluxo completo de pedido
  (endereço → descoberta → carrinho → pagamento → pedido → rastreio).
- **`annotations` estão presentes**, mesmo que inconsistentes (item 6) — o campo existe
  e a intenção é boa.

---

## Recomendações priorizadas

Se eu pudesse escolher apenas três mudanças:

1. **Enxugar o schema das ferramentas** (item 1). É o que impede qualquer uso em tier
   gratuito e hoje força o cliente a cortar metade do servidor. Descrição curta +
   `description` por campo + passo a passo movido para a **resposta**.
2. **Publicar um exemplo de OAuth funcional** e permitir callback configurável
   (itens 2 e 3). Enquanto isso não existe, cada time reimplementa o fluxo — e alguns
   não conseguem.
3. **Declarar `outputSchema`** (item 5). Transforma a integração de "parsear texto" para
   "ler contrato", que é a diferença entre funcionar e funcionar de forma confiável.

---

## Anexo — como reproduzir

```bash
# 1) Confirmar que o endpoint existe e exige OAuth (deve retornar 401 + WWW-Authenticate)
curl -i -X POST https://consumer-mcp.ifood.com.br/marketplace \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 2) Ver a inconsistência de descoberta
curl -s https://consumer-mcp.ifood.com.br/.well-known/oauth-protected-resource/marketplace
curl -s https://id.ifood.com.br/.well-known/openid-configuration          # issuer: .../3p
curl -i https://id.ifood.com.br/3p/.well-known/openid-configuration       # HTTP 400

# 3) Medir o custo do schema (o problema mais caro)
python -c "
import asyncio, json, tiktoken
from agent.mcp import load_mcp_tools
from langchain_core.utils.function_calling import convert_to_openai_tool
enc = tiktoken.get_encoding('o200k_base')
async def main():
    tools = await load_mcp_tools()
    for t in sorted(tools, key=lambda t: -len(json.dumps(convert_to_openai_tool(t)))):
        print(len(enc.encode(json.dumps(convert_to_openai_tool(t)))), t.name)
    print('TOTAL', sum(len(enc.encode(json.dumps(convert_to_openai_tool(t)))) for t in tools))
asyncio.run(main())
"
```

Ambiente dos testes: Windows 11, Python 3.14, `fastmcp` 4.1.0, `mcp` 2.3.0,
`langchain-mcp`, `langchain-openai`. Provedores usados: Groq (tier gratuito),
Gemini 2.5 Flash (tier gratuito) e Ollama 0.40.5 local (`qwen3:8b`, Ryzen 5 5500 +
Radeon RX 6600 com 8 GB de VRAM).
