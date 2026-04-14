---
name: pipeline-judicial-diario
description: Ejecuta el pipeline judicial diario de ASERGES. Descarga correos de procuradores desde Gmail (MCP gmail), clasifica PDFs judiciales con regex + LLM fallback, los copia a Notificaciones y a Usu2, crea eventos en Google Calendar para plazos procesales y envía correo de resumen. Usar cuando se solicite procesar notificaciones judiciales, clasificar PDFs de procuradores, o ejecutar el pipeline judicial diario.
---

# Pipeline Judicial Diario - ASERGES (v2.0)

Automatiza la descarga, clasificación y distribución de notificaciones judiciales recibidas por correo electrónico de procuradores autorizados.

## Flujo de Ejecución

El pipeline judicial diario consta de 6 pasos secuenciales. Ejecuta estos pasos en orden:

1. **Buscar correos de procuradores** (Gmail MCP)
2. **Extraer thread IDs** de los mensajes relevantes
3. **Descargar PDFs** (Gmail MCP)
4. **Preparar archivo temporal** para el script
5. **Ejecutar el pipeline de clasificación** (Python)
6. **Entregar resultados** al usuario

### Paso 1: Buscar correos de procuradores

Busca los correos de los procuradores autorizados en el rango de fechas solicitado:

```bash
manus-mcp-cli tool call gmail_search_messages --server gmail \
  --input '{"q": "from:solasortega.com OR from:procuradoracarmencarrasco@gmail.com OR from:belengoni.com OR from:pazmontero.com has:attachment after:YYYY/MM/DD before:YYYY/MM/DD", "max_results": 100}'
```

### Paso 2: Extraer thread IDs

Extrae los IDs de los hilos de los mensajes encontrados en el paso anterior:

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

### Paso 3: Descargar PDFs

**Paso crítico:** Usa `gmail_read_threads` con `include_full_messages: true` para descargar los adjuntos físicamente a `/home/ubuntu/gmail-attachments/{msg_id}/{filename}`.

```bash
manus-mcp-cli tool call gmail_read_threads --server gmail \
  --input '{"thread_ids": ["id1", "id2", ...], "include_full_messages": true}'
```

### Paso 4: Preparar archivo temporal

Copia el resultado del paso 3 al archivo que espera el script de clasificación:

```bash
cp /tmp/manus-mcp/mcp_result_XXXX.json /tmp/gmail_threads_result.json
```

### Paso 5: Ejecutar el pipeline de clasificación

Ejecuta el script principal que procesa los PDFs descargados:

```bash
python3 /home/ubuntu/skills/pipeline-judicial-diario/scripts/pipeline_judicial.py \
  --desde YYYY-MM-DD [--hasta YYYY-MM-DD] [--no-calendar] [--no-email]
```

El script realiza automáticamente:
- Deduplicación por hash SHA-256
- Clasificación con regex + LLM fallback (gpt-4.1-mini)
- Guardado en `Notificaciones/{periodo}/`
- Copia a `Usu2/{cliente}/` según mapeo
- Creación de eventos en Google Calendar
- Generación de informe y envío de correo de resumen

### Paso 6: Entregar resultados

Comprime los resultados y entrégalos al usuario:

```bash
cd /home/ubuntu/judicial_diario && zip -r /home/ubuntu/resultados_judiciales.zip .
```

## Gestión de Expedientes sin Mapeo

Si el informe indica que hay documentos "Sin mapeo":
1. Revisa el archivo `/home/ubuntu/skills/pipeline-judicial-diario/mapeos/pending_mapeo.json`
2. Identifica el procedimiento o NIG
3. Añade la entrada correspondiente en `/home/ubuntu/skills/pipeline-judicial-diario/mapeos/mapeo_expedientes.json`
4. Re-ejecuta el pipeline (Paso 5)

## Solución de Problemas

- **PDFs no encontrados en disco**: Verifica que el Paso 3 se ejecutó correctamente. El directorio `/home/ubuntu/gmail-attachments/` debe existir.
- **`No se encontraron mensajes`**: Verifica que `/tmp/gmail_threads_result.json` existe y tiene `"success": true`.
- **Carpeta Usu2 no encontrada**: Verifica las rutas en `/home/ubuntu/skills/pipeline-judicial-diario/config/config.json`.
- **Calendar ERROR**: Verifica que el servidor MCP `google-calendar` está autenticado.
