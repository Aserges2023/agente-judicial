# Especificación: Notificaciones Judiciales Automatizadas

## Objetivo

Automatizar el procesamiento de notificaciones judiciales recibidas por email de procuradores: guardar PDFs en OneDrive, clasificarlos en expedientes, analizar con IA, crear eventos en calendario y enviar resumen por email.

---

## 1. INFRAESTRUCTURA

### n8n Cloud
- **URL:** https://aserges2026.app.n8n.cloud
- **Workflow principal ID:** iVSmHsFprCiHdS1Y
- **Workflow resumen 12h ID:** ekdB2c1GOYrioqjz
- **Workflow keepalive ID:** Z6kBTvRCKtQDuvUr

### Workflows históricos (desactivados)
- `dvLJbxE7buNqg8eY` - Workflow antiguo v1
- `oZ6fENlJth58Ef6g` - Test batch

---

## 2. CREDENCIALES n8n

| Credencial | ID | Tipo | Para qué |
|---|---|---|---|
| IONOS IMAP - santiago@aserges.es | `pf0lvolsgCgYzC3P` | imap | Leer correos |
| IONOS SMTP - santiago@aserges.es | `bUqowlkYgkqzV6Lm` | smtp | Enviar emails |
| Microsoft OneDrive - Aserges | `T2PFgQvozQ2WfQQs` | microsoftOAuth2Api | Guardar/buscar archivos |
| Google Calendar account | `L57OkPum635lN3it` | googleCalendarOAuth2Api | Crear eventos |
| n8n free OpenAI API credits | `tlBOWv86kYAdlt8t` | openAiApi | IA (backup) |

---

## 3. CUENTAS DE CORREO

### IONOS - Email principal
- **Email:** santiago@aserges.es
- **Servidor IMAP:** imap.ionos.es (puerto 993, SSL)
- **Servidor SMTP:** smtp.ionos.es (puerto 465, SSL)

### OneDrive - Cuenta profesional
- **Cuenta:** santiago.palacios@aserges.es
- **SharePoint:** aserges2023-my.sharepoint.com

### Google Calendar
- **Calendario:** primary

---

## 4. PROCURADORES AUTORIZADOS

```
@solasortega.com
procuradoracarmencarrasco@gmail.com
@pazmontero.com
@belengoni.com
notificacioneslexnet@justicia.es
```

**Detección de nuevos procuradores:** Emails con adjuntos que contengan keywords judiciales (procurad, notificac, juzgado, tribunal, judicial, emplazamiento, citacion, resolucion, cedula) generan alerta por email.

---

## 5. ESTRUCTURA OneDrive

### Notificaciones (guardado inmediato)
```
DOCS-MNPROGRAM_1631/Notificaciones/YYYY/YYYY-MM/FECHA_NOMBRE_ORIGINAL.pdf
```

### Notificaciones (guardado clasificado por IA)
```
DOCS-MNPROGRAM_1631/Notificaciones/YYYY/YYYY-MM/FECHA_NIG_TIPO_RESOLUCION.pdf
```

### Expedientes (clasificado por cliente)
```
DOCS-MNPROGRAM_1631/Usu2/[NOMBRE_CLIENTE]/Exp[N]/FECHA_NIG_TIPO.pdf
```

### Búsqueda de cliente
1. Extraer partes (demandado/ejecutado) del análisis IA
2. Normalizar nombre (quitar acentos, formas jurídicas SL/SA)
3. Buscar en OneDrive Graph API: `search(q='TERMINO')` dentro de `Usu2/`
4. Matching por puntuación (umbral >= 4 puntos)
5. Si múltiples expedientes, buscar NIG en archivos existentes
6. Si NIG no encontrado: NO archivar automáticamente, enviar alerta

---

## 6. ANÁLISIS IA - CONFIGURACIÓN CLAUDE

### Endpoint
```
POST https://api.anthropic.com/v1/messages
Headers:
  x-api-key: [configurada en n8n]
  anthropic-version: 2023-06-01
  Content-Type: application/json
```

### Modelo: claude-haiku-4-5 (~$0.002/documento)

### Modos de análisis
1. **Texto** (text.length > 50): Envía texto extraído del PDF
2. **Vision PDF** (texto vacío, binario disponible): Envía PDF como base64 via `type: "document"`
3. **Sin datos** (ni texto ni binario): Responde con nulls

### Metadatos extraídos
- nig, numero_autos, tipo_procedimiento, tipo_resolucion
- juzgado, localidad_juzgado, fecha_resolucion
- plazo_dias, fecha_limite_actuacion (recalculada en días hábiles)
- partes (demandante, demandado, ejecutante, ejecutado, etc.)
- objeto_procedimiento, cuantia_euros, letrado_contrario, resumen

---

## 7. CÁLCULO DE PLAZOS

- **Días hábiles judiciales** (no naturales)
- Excluye: sábados, domingos
- Excluye: agosto completo (inhábil judicial)
- Excluye: festivos nacionales (01-01, 01-06, 05-01, 08-15, 10-12, 11-01, 12-06, 12-08, 12-25)
- Excluye: Jueves/Viernes Santo (calculado dinámicamente con algoritmo de Computus)

Ver implementación: `scripts/plazos-judiciales.js`

---

## 8. GOOGLE CALENDAR

### Evento creado cuando hay plazo
- **Título:** `PLAZO: [tipo_resolucion] | [demandado] | [juzgado]`
- **Hora:** 08:00-08:30 Europe/Madrid en fecha_limite_actuacion
- **Recordatorios:** 24h y 1h antes
- **Descripción:** NIG, autos, juzgado, partes, resumen, ruta archivo
- **Deduplicación:** Busca eventos existentes por NIG y fecha antes de crear

---

## 9. EMAILS GENERADOS

| Email | Cuándo |
|---|---|
| Email Verificación Completa | Documento procesado y archivado en expediente |
| Email Verificar Expediente | NIG no encontrado, solo guardado en Notificaciones |
| Email Alerta Sin Clasificar | Cliente no identificado |
| Alerta Fallo IA | Claude falló al analizar |
| Alerta Fallo Notificaciones | Error al subir a OneDrive |
| Alerta Procurador Desconocido | Email judicial de remitente no registrado |
| Alerta Email Sin PDF | Procurador envió email sin adjuntos PDF |
| Email Confirmación Calendario | Evento creado/fallido en Calendar |
| **Resumen cada 12h** | L-V 7:00 y 19:00, resumen de todo lo procesado |

**Remitente/Destinatario:** santiago@aserges.es

---

## 10. FLUJO COMPLETO (38 nodos)

```
IMAP (santiago@aserges.es)
  → Filtrar Procurador → Es Procurador Conocido?
    ├─ SÍ → Separar PDFs → Tiene PDF?
    │   ├─ SÍ (paralelo):
    │   │   ├─ Guardar PDF Inmediato en Notificaciones (PRIORITARIO)
    │   │   └─ Extraer Texto → Preparar Request Claude → Claude Haiku
    │   │       ├─ OK → Parsear → Recalcular Plazos Hábiles → 3 ramas:
    │   │       │   ├─ Guardar en Notificaciones (nombre clasificado)
    │   │       │   ├─ Buscar Cliente → Identificar → ¿Archivado?
    │   │       │   │   ├─ SÍ → Guardar Expediente → Email OK
    │   │       │   │   └─ NO → Email Verificar Manual
    │   │       │   └─ ¿Tiene Plazo? → Dedup → Crear Evento Calendar
    │   │       └─ ERROR → Salvamento (guardar con nombre genérico) → Alerta IA
    │   └─ NO (sin PDF) → Alerta Sin PDF
    └─ NO (pero parece judicial) → Alerta Procurador Desconocido
```

---

## 11. PROBLEMAS CONOCIDOS

1. **IMAP no reprocesa emails antiguos** - El trigger IMAP de n8n Cloud solo procesa emails NUEVOS (UID > ultimo procesado). No hay forma via API de forzar reprocesamiento de emails históricos.
2. **PDFs escaneados** - Los PDFs sin texto extraíble se envían como base64 a Claude Vision. Si el binario se pierde entre nodos, Claude recibe "PDF vacío". Se mitiga referenciando el binario directamente desde el nodo "Separar Adjuntos PDF".
3. **API key hardcodeada** - La key de Claude está en el JSON del workflow, no en el sistema de credenciales de n8n. Pendiente: crear credencial "Header Auth" en la UI de n8n.
4. **Activación via API** - Tras hacer PUT al workflow, a veces el IMAP listener no se reinicia correctamente. Solución: deactivate → esperar 30s → activate.
