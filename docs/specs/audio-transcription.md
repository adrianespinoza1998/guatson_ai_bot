# SPEC: Transcripción de notas de voz con la API de OpenAI (Whisper)

> Spec de una feature incremental sobre el proyecto ya existente (ver `SPEC.md` para el diseño base). Antes de implementar, consulta la documentación vigente del SDK `openai` (async) y de aiogram para descarga de archivos — no asumas firmas de memoria, igual que en `SPEC.md`.

## Objetivo

Cuando el usuario envíe una nota de voz por Telegram, el bot debe:
1. Descargar el audio.
2. Transcribirlo a texto con la API de OpenAI.
3. Procesar ese texto **exactamente igual que un mensaje de texto normal**: mismo loop de agente, mismas tools, mismo historial en `messages`.

No es un modo aparte del bot — es simplemente una forma alternativa de producir el texto que hoy ya viene de `message.text`.

## Alcance

- Aplica a mensajes de tipo `voice` (notas de voz grabadas dentro de Telegram — formato ogg/opus).

## Fuera de alcance (en esta pasada)

- Mensajes de tipo `audio` (archivos subidos, ej. mp3 de una canción o podcast) y `video_note`. Se pueden sumar después reusando la misma función de transcripción; se deja afuera ahora para no ampliar el alcance sin necesidad.
- Imágenes y documentos entrantes: sin cambios, siguen fuera de alcance según `SPEC.md`.
- Guardar el archivo de audio original (ni en disco ni en la base de datos). Solo vive en memoria durante la llamada de transcripción.
- Reintentos automáticos si la transcripción falla. Un fallo se reporta al usuario, igual que cualquier otro error (`SPEC.md` sección 6, paso 6); no se ejecuta ninguna instrucción.
- Selección de idioma explícita: se deja que el modelo autodetecte.

## Configuración nueva (extiende `SPEC.md` sección 4)

| Variable | Descripción |
|---|---|
| `OPENAI_API_KEY` | API key de OpenAI. **Opcional a nivel de arranque de la app** (a diferencia de las demás keys, que si faltan no dejan arrancar): sin ella, el bot sigue funcionando normalmente para texto, y responde a una nota de voz con un mensaje explicando que la transcripción no está disponible. Ver "Decisión" abajo. |
| `OPENAI_TRANSCRIPTION_MODEL` | Default `whisper-1` (el modelo pedido explícitamente). La API de OpenAI también ofrece modelos más nuevos (`gpt-4o-transcribe`, `gpt-4o-mini-transcribe`) con mejor precisión — al ser configurable por variable de entorno, cambiar de modelo no requiere tocar código. |
| `VOICE_MAX_DURATION_SECONDS` | Default `120` (2 minutos). Notas más largas se rechazan sin transcribir — control de costo y latencia. |
| `VOICE_MAX_FILE_BYTES` | Default `20971520` (20 MiB) — el límite de descarga de archivos de la Bot API estándar de Telegram sin servidor local propio. |

**Decisión:** `Settings.openai_api_key` se modela como `str | None` (default `None`), no como campo obligatorio. Las demás keys de la spec original son obligatorias porque sin ellas *nada* del bot funciona; esta es distinta — el bot es completamente usable solo con texto sin ella. Forzarla a obligatoria rompería el arranque de instalaciones que no quieran esta feature.

## Flujo (extiende `SPEC.md` sección 6)

1. Llega un `Update` con `message.voice` en vez de `message.text`.
2. Se valida lista blanca igual que hoy (`app.telegram.handlers.is_allowed`).
3. Antes de decidir qué contenido insertar en `messages`, el bot:
   a. Verifica `voice.duration <= VOICE_MAX_DURATION_SECONDS` y `voice.file_size <= VOICE_MAX_FILE_BYTES`. Si excede alguno, responde con un mensaje corto explicando el límite (ej. "Ese audio dura más de 2 minutos, ..."). No se llama a OpenAI ni se inserta nada en `messages` — se trata como el caso "no soportado" ya existente, no como un turno fallido.
   b. Si `OPENAI_API_KEY` no está configurada: responde "Todavía no puedo transcribir audio en este bot." y termina — mismo tratamiento que (a).
   c. Descarga el archivo con `bot.get_file` + la descarga de aiogram (verificar en la documentación vigente si es `bot.download_file(file_path)` o `bot.download(file)`) a memoria (`BytesIO`), sin escribir a disco.
   d. Llama a la API de transcripciones de OpenAI con esos bytes y `OPENAI_TRANSCRIPTION_MODEL`.
   e. Si la llamada falla (red, audio inentendible, error de la API): se registra el error con `structlog` y se responde al usuario un mensaje corto de error. No se inserta nada en `messages` — mismo criterio que un fallo del agente (nunca dejar estado a medias).
   f. Si tiene éxito: el texto transcrito sigue el mismo camino que hoy sigue `message.text` — `ingest_incoming` → `process_message` → `run_turn`, sin ninguna rama especial de ahí en adelante.
4. El texto guardado en `messages.content` lleva un prefijo que dice que vino de audio, por ejemplo:
   `[nota de voz transcrita, {duration}s] {texto}`
   Esto es solo para que el histórico sea legible para un humano que lo inspeccione — no cambia cómo el agente interpreta el mensaje (Claude simplemente ve texto).

## Modelo de datos

**Sin cambios.** No se agregan columnas ni tablas. El mensaje transcrito se guarda como cualquier mensaje de usuario (rol `user`, un bloque `{"type": "text", "text": ...}`). Se decide así deliberadamente: mantener `messages` como la única fuente de verdad del historial, sin fragmentarlo por tipo de entrada.

## Cambios de código esperados (referencia, no exhaustivo)

- `src/app/config.py`: agregar `openai_api_key: str | None = None`, `openai_transcription_model: str = "whisper-1"`, `voice_max_duration_seconds: int = 120`, `voice_max_file_bytes: int = 20 * 1024 * 1024`.
- Nuevo módulo `src/app/transcription.py`: wrapper delgado sobre `AsyncOpenAI` (SDK oficial `openai`), análogo a `src/app/llm/client.py` para Anthropic — una clase con un único método async (`transcribe(audio_bytes: bytes) -> str`), fácil de reemplazar por un fake en tests (mismo patrón que el `Protocol` `ClaudeClientLike` en `app/llm/agent.py`).
- `src/app/telegram/handlers.py`: `ingest_incoming` gana un tercer caso además de texto/no-soportado: `message.voice is not None` → valida límites, descarga, transcribe, y solo entonces arma el `content` a insertar (o corta temprano con el mensaje de límite/error correspondiente, sin insertar nada).
- `pyproject.toml`: agregar dependencia `openai` (SDK oficial async, mismo criterio que se usó para `anthropic`: no asumir su API de memoria, revisar changelog/docs vigentes antes de escribir el wrapper).
- `.env.example` / `README.md`: documentar las variables nuevas y sumar el mismo aviso de límite de gasto que ya existe para Anthropic, ahora también para la cuenta de OpenAI.

## Seguridad / límites

- Límite de duración y de tamaño de archivo (arriba) para controlar costo y evitar abuso — mismo espíritu que el límite de tamaño del webhook y de los artefactos en `SPEC.md` sección 8.
- El audio nunca se persiste en disco ni en la base de datos; solo existe en memoria durante la llamada de transcripción.
- `OPENAI_API_KEY` nunca se loguea, mismo criterio que las demás keys (`SPEC.md` sección 8).
- Recomendar en el README configurar un límite de gasto en la cuenta de OpenAI, igual que ya se recomienda para Anthropic.

## Tests

- Transcripción exitosa con un cliente OpenAI fake: el texto resultante entra al agent loop igual que un mensaje de texto (reusa los fakes ya existentes en `tests/test_agent_loop.py`).
- Nota de voz que excede `VOICE_MAX_DURATION_SECONDS` o `VOICE_MAX_FILE_BYTES`: se rechaza sin llamar a OpenAI ni al agente; no se inserta nada en `messages`.
- Falla de transcripción (excepción del cliente fake): el usuario recibe un mensaje de error corto y no se inserta nada en `messages`.
- `OPENAI_API_KEY` no configurada: la nota de voz responde con el mensaje de "no disponible" sin romper el resto del bot (los mensajes de texto normales siguen funcionando en el mismo test run).
- Los tests **no** llaman a la API real de OpenAI, mismo criterio que ya aplica para Anthropic y Telegram.

## Fuera de alcance / fases posteriores

- Mensajes de tipo `audio` (archivos) y `video_note`.
- Selección de idioma o de modelo por el usuario vía comando.
- Guardar el audio original como artifact descargable.
- Diarización de hablantes (`gpt-4o-transcribe-diarize`) — no aplica a un bot de un solo usuario.

---

**Nota abierta para quien apruebe esta spec antes de implementar:** se asume que quieres sumar OpenAI como segundo proveedor externo (hoy el proyecto solo depende de Anthropic). Si prefieres transcribir con el propio Claude en vez de agregar un segundo proveedor, hay que rediseñar esta spec — Claude soporta expandirse a otras modalidades, pero convendría confirmarlo puntualmente antes de implementar, ya que cambiaría la sección de configuración, dependencias y el módulo `transcription.py` por uno que use `app.llm.client.ClaudeClient` en su lugar.
