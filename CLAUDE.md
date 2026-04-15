# Agente Judicial

## Project Overview

Sistema de automatización para el procesamiento de notificaciones judiciales recibidas por email de procuradores. Guarda PDFs en OneDrive, los clasifica en expedientes, analiza con IA (Claude), crea eventos en calendario y envía resúmenes por email.

## Development

- Primary language: JavaScript/Node.js (n8n workflows + utility scripts)
- Run tests before committing changes
- Follow existing code style and conventions

## Remote Control

This project is configured for Claude Code remote control sessions.
To start a remote session:

```bash
claude remote-control --name "Agente Judicial"
```

## Conventions

- Write clear, descriptive commit messages
- Keep code simple and focused
- Prefer editing existing files over creating new ones

## Infrastructure

### n8n Cloud
- **URL:** https://aserges2026.app.n8n.cloud
- **API Base:** https://aserges2026.app.n8n.cloud/api/v1
- **MCP Server:** https://aserges2026.app.n8n.cloud/mcp-server/http

### Workflow IDs
| Workflow | ID | Estado |
|---|---|---|
| Producción v3 | `iVSmHsFprCiHdS1Y` | Activo |
| Resumen 12h | `ekdB2c1GOYrioqjz` | Activo |
| Keepalive OneDrive | `Z6kBTvRCKtQDuvUr` | Activo |
| Workflow antiguo v1 | `dvLJbxE7buNqg8eY` | Desactivado |
| Test batch | `oZ6fENlJth58Ef6g` | Desactivado |

### Credenciales n8n (configuradas en instancia)
| Credencial | ID | Tipo |
|---|---|---|
| IONOS IMAP | `pf0lvolsgCgYzC3P` | imap |
| IONOS SMTP | `bUqowlkYgkqzV6Lm` | smtp |
| Microsoft OneDrive | `T2PFgQvozQ2WfQQs` | microsoftOAuth2Api |
| Google Calendar | `L57OkPum635lN3it` | googleCalendarOAuth2Api |
| OpenAI (backup) | `tlBOWv86kYAdlt8t` | openAiApi |

## Procuradores Autorizados
```
@solasortega.com
procuradoracarmencarrasco@gmail.com
@pazmontero.com
@belengoni.com
notificacioneslexnet@justicia.es
```

## Estructura OneDrive
```
DOCS-MNPROGRAM_1631/
├── Notificaciones/YYYY/YYYY-MM/   (guardado inmediato y clasificado)
└── Usu2/[CLIENTE]/Exp[N]/         (clasificado por expediente)
```

## Flujo Completo
```
IMAP → Filtrar Procurador → ¿Conocido?
  ├─ SÍ → Separar PDFs → ¿Tiene PDF?
  │   ├─ SÍ → Guardar inmediato + Analizar IA → Clasificar → Calendar + Email
  │   └─ NO → Alerta Sin PDF
  └─ NO (parece judicial) → Alerta Procurador Desconocido
```

## Problemas Conocidos
1. IMAP no reprocesa emails antiguos (solo UIDs nuevos)
2. PDFs escaneados requieren Vision (base64)
3. API key Claude hardcodeada en workflow JSON
4. Activación via API requiere deactivate → 30s → activate
