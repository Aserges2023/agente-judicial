---
name: pipeline-judicial-diario
description: Ejecuta el pipeline judicial diario de ASERGES. Descarga correos de procuradores desde Gmail (MCP gmail), clasifica PDFs judiciales, los copia a Notificaciones y a Usu2 en el PC del usuario. Usar cuando se solicite procesar notificaciones judiciales, clasificar PDFs de procuradores, o ejecutar el pipeline judicial diario.
---

# Pipeline Judicial Diario - ASERGES

Automatiza la descarga, clasificación y distribución de notificaciones judiciales recibidas por correo electrónico de procuradores autorizados.

**Fuente de correos:** Gmail MCP (`santiago@aserges.es` migrado de Ionos a Gmail en abril 2026). El conector MCP `gmail` se usa para buscar y descargar los adjuntos PDF.

---

## Flujo de ejecución (Gmail MCP)

### Paso 1: Buscar correos de procuradores con `gmail_search_messages`

Ejecutar directamente desde Manus (herramienta shell):

```bash
manus-mcp-cli tool call gmail_search_messages --server gmail --input '{"q": "from:solasortega.com OR from:procuradoracarmencarrasco@gmail.com OR from:belengoni.com OR from:pazmontero.com has:attachment after:YYYY/MM/DD before:YYYY/MM/DD", "max_results": 100}'
```

Guardar el resultado en `/tmp/gmail_search_result.json`:

```bash
cp /tmp/manus-mcp/mcp_result_XXXX.json /tmp/gmail_search_result.json
```

### Paso 2: Extraer los thread IDs de procuradores

```python
import json
with open('/tmp/gmail_search_result.json') as f:
    data = json.load(f)
threads = data.get('result', {}).get('threads', [])
procuradores = ['solasortega.com', 'procuradoracarmencarrasco@gmail.com', 'belengoni.com', 'pazmontero.com']
thread_ids = []
for t in threads:
    for msg in t.get('messages', []):
        from_addr = msg.get('pickedHeaders', {}).get('from', '')
        if any(p.lower() in from_addr.lower() for p in procuradores):
            tid = t.get('id')
            if tid and tid not in thread_ids:
                thread_ids.append(tid)
print(json.dumps(thread_ids))
```

### Paso 3: Descargar los PDFs con `gmail_read_threads`

**Este es el paso clave**: `gmail_read_threads` con `include_full_messages: true` descarga los adjuntos al directorio `/home/ubuntu/gmail-attachments/{msg_id}/{filename}`.

```bash
manus-mcp-cli tool call gmail_read_threads --server gmail --input '{"thread_ids": ["id1", "id2", ...], "include_full_messages": true}'
```

Guardar el resultado en `/tmp/gmail_threads_result.json`:

```bash
cp /tmp/manus-mcp/mcp_result_XXXX.json /tmp/gmail_threads_result.json
```

### Paso 4: Ejecutar el pipeline de clasificación

```bash
python3 /home/ubuntu/skills/pipeline-judicial-diario/scripts/pipeline_judicial.py --desde YYYY-MM-DD
```

El script lee `/tmp/gmail_threads_result.json`, localiza los PDFs en `/home/ubuntu/gmail-attachments/`, los clasifica y genera el informe.

### Paso 5: Revisar resultados y entregar ZIP

El pipeline genera:
- PDFs renombrados en `Notificaciones\{fecha}\`
- PDFs copiados a subcarpetas de cliente en `Usu2\`
- Informe en `judicial_diario\{fecha}\informe_clasificacion.md`
- Log en `judicial_diario\logs\pipeline_judicial.log`

Comprimir y entregar al usuario para descomprimir en su PC.

### Paso 6: Gestionar archivos sin mapeo

Si hay PDFs sin mapeo (no se encontró carpeta de cliente):
1. Revisar el informe para identificar los archivos pendientes
2. Actualizar `mapeos/mapeo_expedientes.json` con el nuevo expediente
3. Re-ejecutar el pipeline

---

## Configuración

### config/config.json

| Campo | Descripción |
|:---|:---|
| `imap` | **No se usa** (migrado a Gmail MCP). Los campos pueden dejarse vacíos. |
| `procuradores_autorizados` | Lista de dominios/emails de procuradores |
| `rutas_windows.notificaciones` | Ruta a carpeta Notificaciones |
| `rutas_windows.usu2` | Ruta a carpeta Usu2 |

### mapeos/mapeo_expedientes.json

Contiene dos secciones:
- `por_procedimiento`: Mapea `"651/2023"` -> `"Avenida de Colón 3 Decoración, SL"`
- `por_asunto_regex`: Mapea regex del asunto del correo -> carpeta cliente

Para añadir un nuevo expediente:

```json
{
  "por_procedimiento": {
    "686/2025": "Riojastur Calidad, SL"
  }
}
```

---

## Cómo funciona la descarga Gmail MCP

El conector MCP de Gmail descarga automáticamente los adjuntos PDF cuando Manus ejecuta `gmail_read_threads` **directamente** (a través de la herramienta shell integrada). Los adjuntos se guardan en `/home/ubuntu/gmail-attachments/{msg_id}/{filename}`.

**Limitación importante:** El conector MCP solo descarga adjuntos cuando es invocado directamente por Manus. Si se llama desde un subprocess de Python, devuelve un JSON de tipo "unfinished tool call" y los archivos no se descargan. Por eso el flujo es en dos pasos: primero Manus descarga los PDFs, luego el script Python los procesa.

---

## Nomenclatura de archivos

Los PDFs se renombran con el formato:
`HHhMM - PARTE - PROCEDIMIENTO - TIPO.pdf`

Tipos de resolución detectados:
- Auto tasacion costas, Auto admision, Auto ejecucion
- Dil ordenacion, Dil requerimiento, Dil negatoria prueba
- Senalamiento vista, Sentencia definitiva
- NOTIFICACION, Requerimiento, Emplazamiento

---

## Procuradores autorizados

- solasortega.com
- procuradoracarmencarrasco@gmail.com
- belengoni.com
- pazmontero.com

---

## Solución de problemas

| Problema | Solución |
|:---|:---|
| PDFs no encontrados en disco | Verificar que el Paso 3 se ejecutó correctamente desde Manus. El directorio `/home/ubuntu/gmail-attachments/` debe existir. |
| `No se encontraron mensajes` | Verificar que `/tmp/gmail_threads_result.json` existe y tiene `"success": true`. |
| `pdfplumber no instalado` | Ejecutar `sudo pip3 install pdfplumber` |
| Carpeta Usu2 no encontrada | Verificar ruta en config.json |
| PDF sin mapeo | Añadir entrada en `mapeos/mapeo_expedientes.json` |
| MCP devuelve resultado incompleto | El conector Gmail MCP solo puede ser invocado directamente desde el entorno Manus. Verificar que se usa la herramienta shell de Manus, no un subprocess Python. |
