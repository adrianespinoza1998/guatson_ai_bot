# SPEC: Dashboard web en `/`

> Spec de una feature incremental sobre el proyecto ya existente (ver `SPEC.md` para el diseño base). Antes de implementar, consulta la documentación vigente de Jinja2/HTMX y de FastAPI para templates y `HTTPBasic` — no asumas firmas de memoria, igual que en `SPEC.md`.

## Objetivo

Una página web servida por la propia app FastAPI (`GET /`) para:
1. **Visualizar** lo que el bot ya guarda: historial de mensajes, tareas, artefactos y uso de tokens.
2. **Administrar** tareas y artefactos con las mismas operaciones que ya expone el agente por Telegram (completar/cancelar una tarea, ver versiones de un artefacto) — nada que el bot no pueda hacer ya, solo una segunda forma de hacerlo.

No es un producto nuevo: reutiliza `repositories/` tal cual existen hoy.

## Alcance

- Página de resumen (`/`): selector de chat (ver "Multi-chat" abajo), conteo de tareas por estado, últimos mensajes, uso de tokens.
- Vista de tareas (`/tasks`): lista filtrable por estado/tags (reusa `repositories.tasks.search_tasks`), botón para completar/cancelar una tarea (reusa `repositories.tasks.update_task` — la misma función que ya usa la tool `update_task`).
- Vista de artefactos (`/artifacts`): lista de artefactos por chat, versiones de cada uno, y descarga del código de una versión específica como archivo de texto.
- Vista de uso (`/usage`): conteo de tokens (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`) agregados por día, para los últimos N días. **Solo conteos, sin estimar costo en dólares** — ver "Decisión" abajo.
- Vista de historial (`/history`): últimos mensajes de un chat, en orden cronológico, con indicador visual de qué bloques son `tool_use`/`tool_result` (no hace falta renderizarlos bonito, con que sean legibles alcanza).

## Fuera de alcance (en esta pasada)

- **Borrar** mensajes, tareas o artefactos. El agente tampoco puede borrar nada hoy (no existe esa tool); el dashboard no debe poder hacer más que el propio bot.
- **Editar configuración en caliente** (`AGENT_MAX_ITERATIONS`, `HISTORY_MAX_MESSAGES`, etc.). Esas variables son de arranque (`Settings` se cachea con `@lru_cache`); cambiarlas en vivo es un cambio arquitectónico aparte, no algo para sumar de paso acá.
- **Estimar costo en dólares.** Requeriría hardcodear un precio por token que cambia con el tiempo y por modelo — mostrar los conteos crudos es suficiente para esta pasada y no queda obsoleto.
- **Enviar mensajes al bot desde el dashboard** (ej. "escribir como si fuera Telegram"). Es una superficie de entrada nueva al agente, no una de administración/visualización.
- Gráficos — tablas y listas simples alcanzan para v1.
- WebSockets / actualización en vivo — recargar la página alcanza para un dashboard personal.

## Multi-chat

`TELEGRAM_ALLOWED_USER_IDS` admite más de un ID, así que puede haber más de un `chat_id` con datos. **Decisión:** el dashboard muestra un selector simple (dropdown) con los `chat_id` que ya tienen filas en `messages`, sin nombres bonitos (no hay tabla de usuarios) — cada vista se filtra por el `chat_id` seleccionado, igual que ya se filtra todo en `repositories/` hoy. Sin chat seleccionado, se usa el primero que aparezca.

## Autenticación

**Decisión:** HTTP Basic Auth con una única contraseña compartida, nueva variable `DASHBOARD_PASSWORD` (obligatoria — sin ella no debería exponerse la ruta, ver más abajo). Comparación en tiempo constante (`hmac.compare_digest`), mismo criterio que ya se usa para `TELEGRAM_WEBHOOK_SECRET` en `app/telegram/webhook.py`. Es deliberadamente simple (sin sesiones, sin usuarios, sin rate limiting) porque es para una sola persona y el tráfico ya va por HTTPS (Fly fuerza `force_https`); no es el nivel de seguridad que tendría un producto multiusuario.

Si `DASHBOARD_PASSWORD` no está configurada, **la ruta `/` (y todas las del dashboard) devuelven 404** en vez de servir la página sin protección — el dashboard es opt-in, no algo que quede accidentalmente abierto en una instalación que no lo configuró.

## Configuración nueva

| Variable | Descripción |
|---|---|
| `DASHBOARD_PASSWORD` | `str \| None = None`. Sin ella, las rutas del dashboard no existen (404). Usuario fijo `admin` (no configurable — es una sola persona). |
| `DASHBOARD_USAGE_DAYS` | Default `30`. Ventana de días hacia atrás para la vista `/usage`. |

## Cambios de código esperados (referencia, no exhaustivo)

- `pyproject.toml`: agregar `jinja2` como dependencia. HTMX se sirve como un único archivo estático vendorizado (sin build step ni npm) — no es una dependencia de Python.
- Nuevo paquete `src/app/dashboard/`:
  - `auth.py`: dependencia FastAPI `require_dashboard_auth` (`HTTPBasic`, compara usuario/password con `hmac.compare_digest`, `401` si falla, `404` si `DASHBOARD_PASSWORD` no está seteada).
  - `routes.py`: `APIRouter` con `/`, `/tasks` (+ `POST /tasks/{id}/complete`, `POST /tasks/{id}/cancel`), `/artifacts` (+ `GET /artifacts/{name}/versions/{version}/download`), `/usage`, `/history` — todas detrás de `require_dashboard_auth`.
  - `templates/`: Jinja2 (`base.html` + una plantilla por vista). Sin CSS framework — algo mínimo a mano alcanza.
- Nueva consulta en `repositories/messages.py`: agregación de tokens por día para un `chat_id` (`SELECT date_trunc('day', created_at), sum(input_tokens), ...`).
- Nueva consulta en `repositories/messages.py` (o donde tenga más sentido): `chat_ids` distintos que aparecen en `messages`, para poblar el selector.
- `src/app/main.py`: montar el router del dashboard y los templates (Jinja2Templates apuntando a `src/app/dashboard/templates`).

## Modelo de datos

**Sin cambios.** Todo lo que el dashboard muestra ya existe en `messages`, `tasks`, `artifacts` y `artifact_versions`. No se agregan columnas ni tablas.

## Seguridad

- `DASHBOARD_PASSWORD` nunca se loguea, mismo criterio que las demás keys/secrets (`SPEC.md` sección 8).
- Comparación de credenciales en tiempo constante, mismo patrón que la validación del webhook.
- Sin `DASHBOARD_PASSWORD` configurada, las rutas ni existen (404, no un 401 que revele que ahí hay algo — mismo espíritu que "no revelar que el bot existe" para usuarios no permitidos en Telegram, `SPEC.md` sección 6 paso 2).
- Las acciones de escritura (completar/cancelar tarea) pasan por el mismo `repositories.tasks.update_task` que ya usa la tool — no hay un segundo camino de validación que mantener sincronizado.

## Tests

- `GET /` sin credenciales → `401` (con `DASHBOARD_PASSWORD` seteada) o `404` (sin ella).
- `GET /` con credenciales correctas → `200`, contiene el selector de chats.
- `GET /tasks` filtra por el `chat_id` del selector — una tarea de otro chat no aparece (mismo criterio de aislamiento que ya prueba `tests/test_tools_tasks.py`).
- `POST /tasks/{id}/complete` cambia el estado a `done` y `completed_at` queda seteado (reusa las mismas aserciones que ya existen para `update_task`).
- `GET /artifacts/{name}/versions/{version}/download` devuelve el código exacto de esa versión.
- `GET /usage` agrega tokens correctamente por día para un `chat_id` con mensajes de assistant conocidos.
- Los tests **no** llaman a la API real de Anthropic ni de Telegram — son solo lecturas/escrituras a Postgres detrás de HTTP con Basic Auth de prueba.

## Fases posteriores

- Estimación de costo en dólares (con una tabla de precios por modelo, actualizable).
- Vista de debug para inspeccionar `tool_use`/`tool_result` con syntax highlighting.
- Edición de configuración en caliente (requeriría mover `Settings` de env-only a algo respaldado por base de datos — cambio arquitectónico, no algo para esta pasada).
- Borrado de datos (con confirmación) si en algún momento se agrega una tool de borrado al agente — se mantiene la simetría "el dashboard no hace más que el bot".

---

**Nota abierta para quien apruebe esta spec antes de implementar:** asumí un dashboard mínimo (tablas HTML + HTMX para las acciones, sin CSS framework ni JS framework) porque es lo consistente con el resto del proyecto (sin frontend hasta ahora). Si preferís algo con más diseño (Tailwind, un framework de componentes) o servido aparte del backend FastAPI, avisá antes de implementar — cambia bastante `main.py` y la estructura de `src/app/dashboard/`.
