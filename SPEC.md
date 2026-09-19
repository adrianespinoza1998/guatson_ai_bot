# SPEC: Asistente personal en Telegram con Claude

> Documento para Claude Code. Tu tarea en esta primera pasada es **inicializar el proyecto** según esta spec: estructura, dependencias, esquema de base de datos, esqueleto funcional de extremo a extremo, tests, Docker y CI/CD. No despliegues nada ni crees recursos en la nube; deja todo listo para hacerlo.
>
> Antes de escribir código que dependa de una librería (aiogram, anthropic, SQLAlchemy, FastAPI), consulta su documentación actual. No asumas firmas de API de memoria. Si algo de esta spec es ambiguo o contradictorio, elige la opción más simple, anótala en `docs/DECISIONS.md` y sigue.

---

## 1. Objetivo

Un bot de Telegram de uso personal conectado a Claude que permite, solo con mensajes de texto:

1. **Guardar y buscar tareas** ("anota: renovar el certificado SSL el viernes", "qué tengo pendiente de infraestructura").
2. **Crear piezas de software sencillas** ("hazme un script que renombre archivos por fecha"). El código se guarda versionado y se envía como archivo.
3. **Conservar todo el histórico** de mensajes (entrada, salida, tool calls y tool results) en Postgres.
### Fuera de alcance (MVP)

- Ejecutar el código generado. En el MVP solo se guarda y se envía como archivo. La ejecución en sandbox es una fase posterior.
- Multiusuario real. El bot atiende a una lista blanca de IDs de Telegram (uso personal).
- Búsqueda semántica (pgvector). Se usa full-text search de Postgres.
- Mensajes de voz, imágenes y documentos entrantes. Si llegan, el bot responde que aún no los soporta.
- Panel web.
---

## 2. Stack

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.12 |
| Gestor de dependencias | `uv` (con `uv.lock` versionado) |
| Web | FastAPI + uvicorn |
| Telegram | aiogram 3 (solo modo webhook en producción; polling para desarrollo local) |
| LLM | SDK oficial `anthropic` (async) con tool use y prompt caching |
| BD | PostgreSQL 16, SQLAlchemy 2.x async (driver `asyncpg`), Alembic |
| Config | `pydantic-settings` |
| Logging | `structlog`, salida JSON |
| Calidad | ruff (lint + format), mypy (strict en `src/`), pytest + pytest-asyncio |
| Contenedores | Docker (multi-stage) y `docker-compose.yml` para desarrollo |
| Despliegue objetivo | Fly.io (región `scl`) + Postgres administrado (Neon o Supabase) |
| CI/CD | GitHub Actions |

---

## 3. Estructura del repositorio

```
.
├── SPEC.md
├── README.md
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── docker-compose.yml
├── fly.staging.toml
├── fly.production.toml
├── alembic.ini
├── .env.example
├── .gitignore
├── .dockerignore
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
├── docs/
│   └── DECISIONS.md
├── migrations/
│   ├── env.py                # async
│   └── versions/
├── scripts/
│   ├── set_webhook.py        # registra webhook + secret_token según entorno
│   └── run_polling.py        # solo desarrollo local
├── src/app/
│   ├── main.py               # create_app(), lifespan, rutas
│   ├── config.py             # Settings
│   ├── logging.py
│   ├── db.py                 # engine, session factory
│   ├── models.py              # modelos SQLAlchemy
│   ├── telegram/
│   │   ├── webhook.py        # POST /webhook/telegram
│   │   ├── handlers.py       # aiogram router
│   │   └── sender.py         # split de mensajes largos, envío de documentos
│   ├── llm/
│   │   ├── client.py         # wrapper del SDK, cache_control
│   │   ├── agent.py          # loop de tool use
│   │   └── prompts.py        # system prompt
│   ├── tools/
│   │   ├── registry.py       # registro + despacho + validación de inputs
│   │   ├── tasks.py
│   │   └── artifacts.py
│   ├── repositories/
│   │   ├── messages.py
│   │   ├── tasks.py
│   │   └── artifacts.py
│   └── queue.py              # interfaz enqueue(); implementación inicial: BackgroundTasks
└── tests/
    ├── conftest.py           # Postgres real (service container en CI, compose en local)
    ├── test_webhook.py
    ├── test_agent_loop.py
    ├── test_tools_tasks.py
    ├── test_tools_artifacts.py
    └── test_history.py
```

Convención: identificadores, nombres de tools y comentarios en **inglés**; textos de cara al usuario y system prompt en **español**.

---

## 4. Configuración

Todo por variables de entorno, validadas al arrancar con `pydantic-settings`. Si falta una obligatoria, la app no arranca.

| Variable | Descripción |
|---|---|
| `APP_ENV` | `local` \| `staging` \| `production` |
| `DATABASE_URL` | `postgresql+asyncpg://...` |
| `TELEGRAM_BOT_TOKEN` | Token de BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Valor para `secret_token` de `setWebhook` |
| `TELEGRAM_ALLOWED_USER_IDS` | Lista separada por comas de IDs permitidos |
| `PUBLIC_BASE_URL` | URL pública, la usa `set_webhook.py` |
| `ANTHROPIC_API_KEY` | API key |
| `CLAUDE_MODEL` | Default `claude-sonnet-5` |
| `CLAUDE_MAX_TOKENS` | Default `4096` |
| `HISTORY_MAX_MESSAGES` | Default `40` |
| `AGENT_MAX_ITERATIONS` | Default `8` |

`.env.example` documenta todas, sin valores reales. `.env` está en `.gitignore`.

---

## 5. Modelo de datos

Migración inicial de Alembic con estas tablas. Usa `timestamptz` en todos los timestamps.

### `messages`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `bigserial` PK | |
| `chat_id` | `bigint` not null | índice |
| `telegram_update_id` | `bigint` null | **unique** (idempotencia; null para mensajes de assistant y tool results) |
| `role` | `text` not null | check `in ('user','assistant')` |
| `content` | `jsonb` not null | lista de content blocks tal como los usa la API de Anthropic (`text`, `tool_use`, `tool_result`) |
| `text_preview` | `text` null | texto plano para inspección y búsqueda |
| `input_tokens`, `output_tokens` | `int` null | uso reportado por la API (solo assistant) |
| `cache_read_tokens`, `cache_write_tokens` | `int` null | |
| `model` | `text` null | |
| `created_at` | `timestamptz` default `now()` | índice `(chat_id, id)` |

Se guardan **todos** los turnos, incluidos los `tool_use` del assistant y los `tool_result` (rol `user`), para poder reconstruir el contexto exacto.

### `tasks`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `bigserial` PK | |
| `chat_id` | `bigint` not null | |
| `title` | `text` not null | |
| `description` | `text` null | |
| `status` | `text` not null default `'open'` | check `in ('open','done','cancelled')` |
| `due_at` | `timestamptz` null | |
| `tags` | `text[]` not null default `'{}'` | índice GIN |
| `search` | `tsvector` | columna generada con config `spanish` sobre `title`, `description` y `tags`; índice GIN |
| `created_at`, `updated_at` | `timestamptz` | |
| `completed_at` | `timestamptz` null | |

### `artifacts` y `artifact_versions`

`artifacts`: `id`, `chat_id`, `name` (único por `chat_id`), `language` (ej. `python`, `html`, `bash`), `description`, `created_at`.

`artifact_versions`: `id`, `artifact_id` FK, `version` int (único junto con `artifact_id`), `code` text, `message_id` FK a `messages` null, `created_at`.

Guardar una nueva versión de un artefacto existente incrementa `version`; nunca se sobrescribe.

---

## 6. Flujo por mensaje

1. `POST /webhook/telegram` valida el header `X-Telegram-Bot-Api-Secret-Token` contra `TELEGRAM_WEBHOOK_SECRET` (comparación en tiempo constante). Si no coincide → `403`.
2. Parsea el `Update`. Si el `from.id` no está en la lista blanca → responde `200` y no hace nada (sin revelar que el bot existe).
3. Inserta el mensaje de usuario en `messages` con `telegram_update_id`. Si ya existe (conflicto de unicidad) → responde `200` y termina (reintento de Telegram).
4. Responde `200` de inmediato y encola el procesamiento vía `queue.enqueue(...)`. Implementación inicial con `BackgroundTasks`; la interfaz debe permitir cambiarla por una cola en Postgres sin tocar el resto.
5. Procesamiento:
   - Envía la acción `typing` a Telegram.
   - Carga los últimos `HISTORY_MAX_MESSAGES` mensajes del chat. **No cortes por la mitad un par `tool_use`/`tool_result`**: si el recorte cae ahí, retrocede hasta un mensaje de usuario con texto.
   - Loop de agente: llama a Claude con system prompt y tools; si la respuesta trae `tool_use`, ejecuta cada tool, guarda el turno assistant y el turno con los `tool_result`, y vuelve a llamar. Termina cuando no hay más `tool_use` o se llega a `AGENT_MAX_ITERATIONS`.
   - Guarda la respuesta final con el uso de tokens.
   - Envía a Telegram: divide en trozos de máximo 4096 caracteres; los artefactos recién guardados se envían además como documento (`.py`, `.html`, etc.).
6. Si algo falla, registra el error con contexto, responde al usuario un mensaje corto de error y no dejes la conversación en un estado inconsistente (un `tool_use` sin su `tool_result` rompe la siguiente llamada a la API).
### Prompt caching

Marca con `cache_control` el system prompt y la definición de tools (prefijo estable). No incluyas fecha/hora dentro del prefijo cacheado; si el modelo necesita la fecha actual, agrégala en el primer mensaje de usuario del turno.

---

## 7. Tools expuestas a Claude

Cada tool tiene JSON Schema estricto, se valida en el registro antes de ejecutarse y devuelve un `tool_result` en texto o JSON corto. Los errores de validación se devuelven como `tool_result` con `is_error: true`, no como excepciones.

| Tool | Propósito | Inputs principales |
|---|---|---|
| `create_task` | Crear tarea | `title`, `description?`, `due_at?` (ISO 8601), `tags?` |
| `search_tasks` | Buscar tareas | `query?`, `status?` (default `open`), `tags?`, `limit?` (default 10, máx. 25) |
| `update_task` | Editar, completar o cancelar | `task_id`, campos opcionales, incluido `status` |
| `save_artifact` | Guardar código nuevo o nueva versión | `name`, `language`, `code`, `description?` |
| `get_artifact` | Recuperar la última versión (o una específica) | `name`, `version?` |
| `list_artifacts` | Listar artefactos del chat | `limit?` |

- Todas las consultas filtran por `chat_id`. El `chat_id` lo inyecta el backend, **nunca** lo decide el modelo.
- `search_tasks` usa `websearch_to_tsquery('spanish', ...)` sobre la columna `search`.
- Las zonas horarias se interpretan en `America/Santiago` cuando el usuario no especifica otra; se guardan en UTC.
### System prompt (en `prompts.py`, en español)

Debe indicar, como mínimo: que es un asistente personal por Telegram, respuestas breves y directas, uso de las tools para persistir tareas y código en lugar de "recordar" en la conversación, confirmar en una línea lo que guardó, pedir aclaración solo si falta información imprescindible, y que al generar software debe llamar a `save_artifact` con código completo y ejecutable (no fragmentos) y explicar en pocas líneas cómo usarlo.

---

## 8. Seguridad

- Lista blanca de usuarios (sección 6, paso 2). Sin excepciones.
- Validación del `secret_token` del webhook.
- Ningún secreto en el repo, en logs ni en imágenes Docker. Los logs nunca imprimen el token del bot ni la API key.
- El código generado por Claude **no se ejecuta** en el servidor en ningún punto de este MVP.
- Contenedor con usuario no root.
- Límite de tamaño para el `code` de un artefacto (por ejemplo 200 KB) y para el cuerpo del webhook.
- Documenta en el README que se debe configurar un límite de gasto en la consola de Anthropic.
---

## 9. Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/webhook/telegram` | Webhook de Telegram |
| `GET` | `/health` | Liveness: `200 {"status":"ok"}` sin tocar la BD |
| `GET` | `/health/ready` | Readiness: ejecuta `SELECT 1`; `503` si falla |

---

## 10. Docker y entorno local

- `Dockerfile` multi-stage: etapa de build con `uv` que instala dependencias desde `uv.lock` (`--frozen`), etapa final `python:3.12-slim` con usuario no root. Comando: `uvicorn app.main:app --host 0.0.0.0 --port 8080`.
- `docker-compose.yml`: servicios `db` (postgres:16, volumen persistente, healthcheck) y `app` (con `--reload`, montando `src/`). Un perfil `polling` que corre `scripts/run_polling.py` para desarrollar sin exponer un webhook.
- `scripts/set_webhook.py`: lee `PUBLIC_BASE_URL`, `TELEGRAM_BOT_TOKEN` y `TELEGRAM_WEBHOOK_SECRET`, llama a `setWebhook` con `secret_token` y `allowed_updates=["message"]`, e imprime el resultado de `getWebhookInfo`. Debe ser idempotente.
---

## 11. CI/CD (GitHub Actions)

Modelo trunk-based: `main` protegida, cambios solo por PR.

### `.github/workflows/ci.yml` (en PR y en push a `main`)

1. Checkout, instalar `uv`, `uv sync --frozen`.
2. `ruff check` y `ruff format --check`.
3. `mypy src`.
4. `pytest` con un service container `postgres:16`; aplica `alembic upgrade head` antes de los tests.
5. `docker build` (sin push) para verificar que la imagen compila.
### `.github/workflows/deploy.yml` (al terminar CI en `main`)

1. **Staging** (GitHub Environment `staging`): `flyctl deploy --remote-only --config fly.staging.toml --image-label ${{ github.sha }}`, con las migraciones como `release_command` (`alembic upgrade head`) en el `fly.*.toml`.
2. Smoke test: `curl --fail` a `/health/ready` de staging con reintentos.
3. Ejecuta `scripts/set_webhook.py` contra staging.
4. **Producción** (GitHub Environment `production`, con aprobación manual requerida): mismo procedimiento con `fly.production.toml`.
Reglas de migraciones: siempre compatibles hacia atrás (agregar → migrar datos → eliminar en un release posterior), para poder revertir la app sin romper el esquema.

Secretos por entorno en GitHub Environments: `FLY_API_TOKEN`, y en Fly (`fly secrets`) las variables de la sección 4. Staging usa **otro bot** (otro token) y **otra base de datos**.

Los `fly.*.toml` deben incluir región `scl`, puerto interno 8080, `[checks]` sobre `/health/ready` y `release_command`. Deja los nombres de app como placeholders claramente marcados.

---

## 12. Tests mínimos (deben pasar en CI)

- **Webhook:** `403` con secret incorrecto; `200` sin efectos para usuario no permitido; el mismo `update_id` enviado dos veces se procesa una sola vez.
- **Agent loop:** con un cliente de Anthropic falso (fake), verifica que una respuesta con `tool_use` ejecuta la tool, guarda los tres turnos en orden y termina; que se respeta `AGENT_MAX_ITERATIONS`; que un error en una tool produce un `tool_result` con `is_error`.
- **Historial:** el recorte a `HISTORY_MAX_MESSAGES` nunca deja un `tool_result` sin su `tool_use`.
- **Tasks:** crear, buscar (FTS en español, con y sin acentos), completar, y aislamiento por `chat_id`.
- **Artifacts:** guardar, versionar (v1 → v2), recuperar una versión específica, aislamiento por `chat_id`.
- Los tests **no** llaman a la API real de Anthropic ni a Telegram.
---

## 13. Entregables de esta pasada

Marca cada punto al terminarlo y haz commits pequeños y descriptivos (Conventional Commits).

- [ ] Repo inicializado con la estructura de la sección 3, `pyproject.toml` y `uv.lock`.
- [ ] `config.py` con validación y `.env.example` completo.
- [ ] Modelos y migración inicial de Alembic (incluida la columna `tsvector` generada y los índices).
- [ ] Webhook con validación de secret, lista blanca e idempotencia.
- [ ] Agent loop con tool use, prompt caching y persistencia completa del historial.
- [ ] Las seis tools de la sección 7 con sus repositorios.
- [ ] Envío a Telegram con división de mensajes largos y envío de artefactos como documento.
- [ ] `scripts/set_webhook.py` y `scripts/run_polling.py`.
- [ ] `Dockerfile` y `docker-compose.yml`; `docker compose up` deja el bot funcionando en modo polling.
- [ ] `ci.yml` y `deploy.yml`, más `fly.staging.toml` y `fly.production.toml` con placeholders.
- [ ] Tests de la sección 12 pasando en local.
- [ ] `README.md` con: requisitos, cómo crear el bot con BotFather, cómo obtener tu `user_id`, cómo correr en local, cómo configurar staging/producción y secretos, y el aviso sobre límites de gasto.
- [ ] `docs/DECISIONS.md` con cualquier decisión que hayas tomado por ambigüedad.
### Criterio de aceptación

Con `.env` completo y `docker compose --profile polling up`, enviar por Telegram "anota: revisar backups el lunes" crea una tarea; "qué tengo pendiente" la devuelve; "hazme un script en python que cuente líneas de un archivo" guarda un artefacto y lo envía como `.py`. Todo el intercambio queda en `messages`. `ruff`, `mypy` y `pytest` pasan sin errores.

---

## 14. Fases posteriores (no implementar ahora)

1. Ejecución de artefactos en sandbox aislado (E2B, contenedor sin red, o code execution de la API).
2. Cola durable sobre Postgres (pgqueuer) en lugar de `BackgroundTasks`.
3. Resumen automático del historial antiguo para controlar costos.
4. Recordatorios programados para tareas con `due_at`.
5. Búsqueda semántica con pgvector.
