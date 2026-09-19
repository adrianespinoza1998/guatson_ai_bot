# guatson-bot-ai

Asistente personal en Telegram conectado a Claude: anota y busca tareas, y genera
piezas de software sencillas que guarda versionadas y envía como archivo. Todo el
histórico de la conversación (mensajes, tool calls y tool results) queda en Postgres.

Ver [`SPEC.md`](SPEC.md) para el diseño completo y [`docs/DECISIONS.md`](docs/DECISIONS.md)
para las decisiones tomadas por ambigüedad durante la implementación.

## Requisitos

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/) como gestor de dependencias
- Docker y Docker Compose (para Postgres local y para correr todo en contenedores)
- Una cuenta de Telegram y acceso a [@BotFather](https://t.me/BotFather)
- Una API key de Anthropic

## Crear el bot con BotFather

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot` y sigue las instrucciones (nombre, username terminado en `bot`).
3. BotFather te entrega un token con forma `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   Ese es tu `TELEGRAM_BOT_TOKEN`.
4. (Opcional pero recomendado) Envía `/setprivacy` → elige tu bot → `Disable`, para
   que reciba todos los mensajes del chat sin restricciones de grupo (no aplica si lo
   usas solo en chat privado contigo mismo, que es el caso de uso previsto).

## Obtener tu `user_id` de Telegram

Este bot es de uso personal: solo responde a los IDs listados en
`TELEGRAM_ALLOWED_USER_IDS`. Para obtener el tuyo, habla con
[@userinfobot](https://t.me/userinfobot) (te responde tu ID numérico), o revisa el
campo `from.id` de cualquier update una vez que tengas el bot corriendo.

## Correr en local

```bash
cp .env.example .env
# Completa .env: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, ANTHROPIC_API_KEY, etc.
# TELEGRAM_WEBHOOK_SECRET y PUBLIC_BASE_URL no se usan en modo polling, pero deben
# tener algún valor porque la configuración los exige.

docker compose --profile polling up --build
```

Esto levanta Postgres, aplica las migraciones (`alembic upgrade head`) y arranca el bot
en modo *long-polling* (sin necesidad de exponer una URL pública). Escríbele por
Telegram:

- `anota: revisar backups el lunes` → crea una tarea.
- `qué tengo pendiente` → la busca y la devuelve.
- `hazme un script en python que cuente líneas de un archivo` → guarda un artefacto y
  lo envía como `.py`.

### Sin Docker

```bash
uv sync
export $(grep -v '^#' .env | xargs)  # o usa un cargador de .env de tu shell
uv run alembic upgrade head
uv run python scripts/run_polling.py
```

### Calidad de código

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Los tests necesitan una instancia real de Postgres apuntada por `DATABASE_URL`
(por ejemplo, `docker compose up db` y usar esa misma URL). No llaman a la API real de
Anthropic ni a Telegram.

## Staging y producción

El despliegue objetivo es [Fly.io](https://fly.io) (región `scl`) con Postgres
administrado (Neon o Supabase) y CI/CD por GitHub Actions
(`.github/workflows/ci.yml` y `deploy.yml`).

1. Crea dos apps en Fly (una por entorno) y actualiza los placeholders `app = "..."`
   en `fly.staging.toml` y `fly.production.toml`.
2. Crea dos bots de Telegram distintos (BotFather) — staging y producción no deben
   compartir token ni base de datos.
3. Configura los secretos de cada entorno con `flyctl secrets set` (una vez por app),
   usando las variables de la sección "Configuración" de `SPEC.md`.
4. En GitHub, crea los *Environments* `staging` y `production` (Settings → Environments)
   y agrega el secreto `FLY_API_TOKEN` a cada uno. En `production`, agrega una regla de
   aprobación manual (*required reviewers*).
5. Un push a `main` (tras pasar CI) despliega a `staging` automáticamente, corre un
   smoke test contra `/health/ready` y registra el webhook (`scripts/set_webhook.py`).
   El despliegue a `production` espera aprobación manual en GitHub.

Las migraciones corren como `release_command` de Fly (`alembic upgrade head`) antes de
que el nuevo release reciba tráfico. Deben ser siempre compatibles hacia atrás
(agregar → migrar datos → eliminar en un release posterior) para poder revertir la app
sin romper el esquema vigente.

## Límite de gasto

**Configura un límite de gasto (spend limit) en la consola de Anthropic** antes de
dejar este bot corriendo sin supervisión: <https://console.anthropic.com/settings/limits>.
Un bug en el loop del agente (por ejemplo, `AGENT_MAX_ITERATIONS` mal configurado) no
debería poder generar una factura sorpresa.

## Fuera de alcance (MVP)

Ver `SPEC.md` secciones 1 y 14: no se ejecuta el código generado, no hay multiusuario
real, no hay búsqueda semántica, no se soportan mensajes de voz/imagen/documento, y no
hay panel web.
