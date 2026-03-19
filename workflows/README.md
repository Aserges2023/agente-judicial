# Workflows n8n

Este directorio contiene las exportaciones JSON de los workflows de n8n para control de versiones.

## Workflows activos

| Archivo | Workflow | ID n8n |
|---|---|---|
| `notificaciones-produccion-v3.json` | Producción principal | `iVSmHsFprCiHdS1Y` |
| `resumen-12h.json` | Resumen cada 12 horas | `ekdB2c1GOYrioqjz` |
| `keepalive-onedrive.json` | Keepalive token OneDrive | `Z6kBTvRCKtQDuvUr` |

## Cómo importar

1. Ir a https://aserges2026.app.n8n.cloud
2. Crear nuevo workflow
3. Importar desde archivo JSON
4. Configurar credenciales (no se exportan por seguridad)

## Notas

- Los JSON de workflows **no contienen credenciales** - se deben configurar manualmente en n8n
- Tras importar, revisar que las credenciales están correctamente asignadas a cada nodo
- Las API keys directas (Claude, Gemini, Groq) están referenciadas en los nodos HTTP Request
