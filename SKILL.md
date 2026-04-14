---
name: pipeline-judicial-diario
description: Ejecuta el pipeline judicial diario de ASERGES. Descarga correos de procuradores desde Gmail (MCP gmail), clasifica PDFs judiciales con regex + LLM fallback, los copia a Notificaciones y a Usu2, crea eventos en Google Calendar para plazos procesales y envía correo de resumen. Usar cuando se solicite procesar notificaciones judiciales, clasificar PDFs de procuradores, o ejecutar el pipeline judicial diario.
---

# Pipeline Judicial Diario - ASERGES (v2.0)

Automatiza la descarga, clasificación y distribución de notificaciones judiciales recibidas por correo electrónico de procuradores autorizados.

**Fuente de correos:** Gmail MCP (`santiago@aserges.es` migrado de Ionos a Gmail en abril 2026).

---

## Mejoras v2.0

| Mejora | Descripción |
|:---|:---|
| **LLM fallback** | Si regex no extrae procedimiento o partes, usa `gpt-4.1-mini` para clasificar |
| **Normalización robusta** | `MON 686-25` → `686/2025`, `0000686/2025` → `686/2025` |
| **Deduplicación por hash** | SHA-256 evita copias duplicadas en Notificaciones y Usu2 |
| **Mapeo dinámico** | Expedientes sin mapeo se registran en `mapeos/pending_mapeo.json` |
| **Mapeo por NIG** | Nuevo nivel de búsqueda: `por_nig` en `mapeo_expedientes.json` |
| **Google Calendar** | Eventos para vistas (09:00) y plazos (08:00 día vencimiento) |
| **Correo de resumen** | Texto plano con tabla de docs, plazos e informe PDF adjunto |
| **Nuevos subtipos** | Acuse presentación, Decreto admisión, Testimonio firmeza, Oficio, etc. |
| **Filtro por fecha** | Solo procesa mensajes dentro del rango `--desde`/`--hasta` |

---

## Flujo de Ejecución (7 Fases)

### Paso 1 — Buscar correos de procuradores

```bash
manus-mcp-cli tool call gmail_search_messages --server gmail \
  --input '{"q": "from:solasortega.com OR from:procuradoracarmencarrasco@gmail.com OR from:belengoni.com OR from:pazmontero.com has:attachment after:YYYY/MM/DD before:YYYY/MM/DD", "max_results": 100}'
```

### Paso 2 — Extraer thread IDs de procuradores

```python
import json
with open('/tmp/manus-mcp/mcp_result_XXXX.json') as f:
    data = json.load(f)
procuradores = ['solasortega.com', 'procuradoracarmencarrasco@gmail.com', 'belengoni.com', 'pazmontero.com']
thread_ids = []
for t in data.get('result', {}).get('threads', []):
    for msg in t.get('messages', []):
        from_addr = msg.get('pickedHeaders', {}).get('from', '')
        if any(p in from_addr.lower() for p in procuradores):
            tid = t.get('id')
            if tid and tid not in thread_ids:
                thread_ids.append(tid)
print(json.dumps(thread_ids))
```

### Paso 3 — Descargar PDFs con `gmail_read_threads`

**Paso clave:** Solo `gmail_read_threads` con `include_full_messages: true` descarga los adjuntos físicamente a `/home/ubuntu/gmail-attachments/{msg_id}/{filename}`.

```bash
manus-mcp-cli tool call gmail_read_threads --server gmail \
  --input '{"thread_ids": ["id1", "id2", ...], "include_full_messages": true}'
```

### Paso 4 — Copiar resultado al archivo temporal

```bash
cp /tmp/manus-mcp/mcp_result_XXXX.json /tmp/gmail_threads_result.json
```

### Paso 5 — Ejecutar el pipeline de clasificación

```bash
python3 /home/ubuntu/skills/pipeline-judicial-diario/scripts/pipeline_judicial.py \
  --desde YYYY-MM-DD [--hasta YYYY-MM-DD] [--no-calendar] [--no-email]
```

El script realiza automáticamente las 7 fases:
1. Lee PDFs desde `/home/ubuntu/gmail-attachments/` (deduplicación por hash SHA-256)
2. Clasifica cada PDF con regex + LLM fallback (gpt-4.1-mini)
3. Guarda en `Notificaciones/{periodo}/` con nombre descriptivo
4. Copia a `Usu2/{cliente}/` según mapeo (procedimiento > NIG > regex asunto > parte)
5. Crea eventos en Google Calendar para vistas y plazos detectados
6. Genera informe Markdown + JSON de estadísticas
7. Envía correo de resumen con informe PDF adjunto (requiere confirmación en UI)

### Paso 6 — Entregar resultados

```bash
cd /home/ubuntu/judicial_diario && zip -r /home/ubuntu/resultados_judiciales.zip .
```

---

## Configuración

### `config/config.json`

```json
{
  "procuradores_autorizados": [
    "solasortega.com",
    "procuradoracarmencarrasco@gmail.com",
    "belengoni.com",
    "pazmontero.com"
  ],
  "rutas_windows": {
    "notificaciones": "C:\\Users\\santi\\OneDrive - Aserges\\DOCS-MNPROGRAM_1631\\Notificaciones",
    "usu2": "C:\\Users\\santi\\OneDrive - Aserges\\DOCS-MNPROGRAM_1631\\Usu2",
    "trabajo": "C:\\Users\\santi\\OneDrive - Aserges\\judicial_diario"
  },
  "mapeo_expedientes_archivo": "mapeos/mapeo_expedientes.json",
  "email_resumen": "santiago@aserges.es",
  "usar_llm_para_clasificar": true,
  "modelo_llm": "gpt-4.1-mini"
}
```

### `mapeos/mapeo_expedientes.json`

```json
{
  "por_procedimiento": {
    "686/2025": "Riojastur Calidad SL",
    "1132/2025": "Antolin Perez SL"
  },
  "por_nig": {
    "26089-41-1-2025-0001234": "Cliente XYZ"
  },
  "por_asunto_regex": {
    "(?i)riojastur": "Riojastur Calidad SL"
  }
}
```

### `mapeos/pending_mapeo.json` (auto-generado)

Expedientes detectados sin mapeo. Rellenar `carpeta_usu2` y mover a `mapeo_expedientes.json`.

---

## Opciones de Línea de Comandos

| Opción | Descripción |
|:---|:---|
| `--desde YYYY-MM-DD` | Fecha inicio del periodo (por defecto: hoy) |
| `--hasta YYYY-MM-DD` | Fecha fin del periodo (por defecto: hoy) |
| `--no-calendar` | No crear eventos en Google Calendar |
| `--no-email` | No enviar correo de resumen |

---

## Nomenclatura de Archivos

Formato: `HHhMM - CLIENTE - PROC-AÑO - TIPO.pdf`

Tipos de resolución detectados:
- Auto tasacion costas, Auto admision, Auto archivo, Auto ejecucion
- Dil ordenacion, Dil requerimiento, Dil negatoria prueba, Dil requerimiento datos, Dil embargo
- Sentencia, Sentencia definitiva
- Providencia, Providencia senalamiento
- Decreto, Decreto admision
- Senalamiento vista, Requerimiento, Emplazamiento
- Acuse presentacion, Testimonio firmeza, Oficio
- NOTIFICACION (por defecto)

---

## Solución de Problemas

| Problema | Solución |
|:---|:---|
| PDFs no encontrados en disco | Verificar que el Paso 3 se ejecutó correctamente. El directorio `/home/ubuntu/gmail-attachments/` debe existir. |
| `No se encontraron mensajes` | Verificar que `/tmp/gmail_threads_result.json` existe y tiene `"success": true`. |
| `pdfplumber no instalado` | Ejecutar `sudo pip3 install pdfplumber` |
| Carpeta Usu2 no encontrada | Verificar ruta en `config.json` |
| PDF sin mapeo | Revisar `mapeos/pending_mapeo.json` y añadir entrada en `mapeo_expedientes.json` |
| LLM fallback falla | Verificar que `OPENAI_API_KEY` está configurada en el entorno |
| Calendar ERROR | Verificar que el servidor MCP `google-calendar` está autenticado |
