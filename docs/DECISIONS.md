# Decisiones de diseño

Registro de decisiones tomadas por ambigüedad o contradicción en `SPEC.md`, en orden cronológico. Regla general aplicada: ante la duda, la opción más simple que cumple el criterio de aceptación.

## Contraseña de Postgres en `docker-compose.yml`

- La versión inicial hardcodeaba `POSTGRES_USER`/`PASSWORD`/`DB` como `guatson`/`guatson`/`guatson` directamente en `docker-compose.yml`, y GitGuardian lo marcó como secreto expuesto en el repo público (aunque era solo la contraseña de un Postgres local, nunca expuesto a internet). Se corrigió reemplazándolo por interpolación de variables (`${POSTGRES_PASSWORD:?...}`, que falla explícitamente si no está seteada) leídas del `.env` de la raíz del proyecto — que Docker Compose carga automáticamente para sustituir `${...}` dentro del propio archivo compose, aparte y además de `env_file:` (que inyecta variables al entorno del contenedor). `.env.example` ahora trae un placeholder `CHANGE_ME_STRONG_PASSWORD` en vez de un valor usable. El valor viejo sigue en el historial de git; ver el mensaje del commit que aplica este cambio para las opciones sobre cómo (o si vale la pena) purgarlo.

## Empaquetado y gestor de dependencias

- Se usa `hatchling` como backend de build con layout `src/`, paquete `app` (`src/app`). El comando de arranque es `uvicorn app.main:app`, tal como pide la spec.
- `uv` no estaba instalado en la máquina de desarrollo; se instaló localmente (`astral.sh/uv/install.sh`) para poder generar `uv.lock` real y correr `ruff`/`mypy`/`pytest` antes de entregar. Esto es una herramienta de desarrollo, no una dependencia del proyecto.
- Python 3.12 tampoco estaba instalado; se resolvió vía `uv python install 3.12`, que `uv` gestiona de forma aislada (no toca el Python del sistema).

## Búsqueda con acentos (`search_tasks`)

- El criterio de aceptación de los tests pide FTS que funcione "con y sin acentos" (ej. `renovación` debe encontrar lo mismo que `renovacion`). La función `unaccent()` de Postgres es `STABLE`, no `IMMUTABLE`, así que no se puede usar directamente dentro de una columna `GENERATED`. Se sigue el patrón documentado en el wiki de PostgreSQL: una función wrapper `immutable_unaccent(text)` marcada `IMMUTABLE` que llama a `unaccent('unaccent', $1)` por dentro. La columna `tasks.search` y el lado de la consulta en `search_tasks()` pasan ambos por esa misma función, así que quedan normalizados de forma consistente.

## Consistencia del turno del agente ante fallos

- La spec exige que "un `tool_use` sin su `tool_result` rompe la siguiente llamada a la API" nunca ocurra, incluso si algo falla a mitad de un turno. En vez de intentar reparar el estado manualmente, todo `run_turn()` corre dentro de una única transacción de `session_scope()`: cada turno de assistant/tool_result se hace `flush()` (visible dentro de la misma transacción) pero nada se hace `commit()` hasta que el turno completo termina con éxito. Si algo inesperado falla (error de red con la API, un bug en un tool handler), la excepción se propaga, la transacción hace rollback completo, y la conversación queda exactamente como estaba antes de que el turno empezara — nunca con un `tool_use` guardado sin su `tool_result`. El mensaje original del usuario no se pierde porque se inserta y confirma en una transacción aparte, en el propio handler del webhook, antes de encolar el procesamiento.

## `queue.py`

- La spec pide una interfaz `enqueue()` que hoy usa `BackgroundTasks` de FastAPI y que "debe permitir cambiarse por una cola en Postgres sin tocar el resto". `BackgroundTasks` es un objeto por-request (lo crea FastAPI por cada request), así que se modela como un `Queue` (Protocol) con un método `enqueue(coro)`, y una implementación `BackgroundTasksQueue` que se construye en el handler del webhook envolviendo el `BackgroundTasks` inyectado por FastAPI en esa request. Una futura cola durable expondría el mismo método `enqueue()` pero insertando una fila en Postgres en vez de agendar una tarea en memoria; el call-site (`webhook.py`) no cambia.

## docker-compose y perfil `polling`

- Se definen tres servicios: `db` (siempre activo), `app` (modo webhook, con `--reload`, activo por defecto) y `bot-polling` (bajo el perfil `polling`, corre `scripts/run_polling.py`). Ambos servicios de aplicación corren `alembic upgrade head` antes de arrancar.
- `docker compose --profile polling up` levanta `db` + `app` + `bot-polling` (los servicios sin perfil siempre se incluyen). Esto es seguro: Telegram solo rechaza usar `getUpdates` si el bot tiene un webhook registrado, y `app` por sí solo no registra ningún webhook (eso lo hace explícitamente `scripts/set_webhook.py`), así que ambos servicios pueden convivir sin conflicto para el criterio de aceptación del MVP.

## Límite de tamaño del webhook

- La spec pide "límite de tamaño ... para el cuerpo del webhook" sin dar un número. Se usa 1 MiB como límite razonable para un update de Telegram con texto (los updates con archivos/voz de todas formas se rechazan por estar fuera de alcance). Se aplica con un middleware ASGI que revisa `Content-Length` y devuelve `413` si se excede.

## Límite de tamaño de artefactos

- La spec sugiere "por ejemplo 200 KB" para el código de un artefacto. Se usa ese valor (`204800` bytes) como límite duro, validado en `tools/registry.py` antes de invocar la tool.

## Zona horaria

- `America/Santiago` se usa para interpretar fechas/horas ambiguas que llegan del usuario (vía el system prompt, ya que la interpretación de lenguaje natural la hace Claude). El campo `due_at` siempre se persiste en UTC (`timestamptz`). No se implementa conversión de horario de verano especial más allá de la que ya provee `zoneinfo`.

## Base de datos de test

- Los tests usan Postgres real, apuntado por la variable de entorno `DATABASE_URL` (igual que la app). En CI, un service container `postgres:16` expone esa URL; en local, se espera `docker compose up db` corriendo y un `DATABASE_URL` de test en `.env` (o exportado en la shell) apuntando a esa instancia. Antes de correr los tests se aplican las migraciones (`alembic upgrade head`). Cada test que toca la base limpia sus propias tablas al final (no hay una base de datos separada por test para mantener el setup simple).

## Modelo de Claude por defecto

- `CLAUDE_MODEL` por defecto es `claude-sonnet-5`, el modelo vigente al momento de esta implementación (septiembre de 2026).

## Cache de prompt

- Se marca con `cache_control: {"type": "ephemeral"}` el último bloque del `system` prompt y la última tool del array `tools`, ya que la API de Anthropic cachea todo el prefijo hasta (e incluyendo) el bloque marcado. La fecha/hora actual nunca entra en ese prefijo cacheado: se antepone al primer mensaje de usuario de cada turno de agente como una línea de contexto separada.

## mypy estricto sobre librerías de terceros

- `aiogram`, `anthropic`, `sqlalchemy`, `structlog` y `pydantic-settings` distribuyen sus propios tipos (`py.typed`), así que `mypy --strict` corre sin `ignore_missing_imports` para ellas. Si en el futuro alguna dependencia deja de tener tipos, la solución es un stub local en `src/app/py.typed`-adjacent `*.pyi`, no relajar `strict` globalmente.
