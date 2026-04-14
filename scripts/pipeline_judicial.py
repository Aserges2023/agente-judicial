#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Judicial Diario - ASERGES
===================================
Descarga correos de procuradores desde Gmail (MCP gmail),
clasifica PDFs judiciales, los copia a Notificaciones y a Usu2.

NOTA: Este script usa el conector MCP de Gmail para buscar y descargar
los correos de procuradores. Debe ejecutarse desde el entorno Manus,
no directamente en el PC del usuario.

Uso:
  python pipeline_judicial.py                    # Procesa correos de hoy
  python pipeline_judicial.py --desde 2026-03-16 # Desde fecha hasta hoy
  python pipeline_judicial.py --desde 2026-03-16 --hasta 2026-03-21
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SCRIPT_DIR / "config" / "config.json"

def cargar_config() -> dict:
    """Carga la configuración desde config.json."""
    if not CONFIG_PATH.exists():
        print(f"ERROR: No se encuentra {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CFG = cargar_config()

# Procuradores (IMAP eliminado - ahora se usa Gmail MCP)
PROCURADORES_AUTORIZADOS = CFG["procuradores_autorizados"]

# Rutas Windows
RUTAS = CFG["rutas_windows"]
NOTIFICACIONES_DIR = Path(RUTAS["notificaciones"])
USU2_DIR = Path(RUTAS["usu2"])
TRABAJO_DIR = Path(RUTAS["trabajo"])

# Mapeo de expedientes
MAPEO_PATH = SCRIPT_DIR / CFG.get("mapeo_expedientes_archivo", "mapeos/mapeo_expedientes.json")

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_DIR = TRABAJO_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "pipeline_judicial.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("pipeline_judicial")

# ─────────────────────────────────────────────
# ESTADÍSTICAS
# ─────────────────────────────────────────────
stats = {
    "correos_revisados": 0,
    "pdfs_descargados": 0,
    "pdfs_clasificados": 0,
    "pdfs_vinculables": 0,
    "pdfs_guardados_notificaciones": 0,
    "pdfs_copiados_usu2": 0,
    "pdfs_sin_mapeo": 0,
    "errores": [],
    "documentos": [],
    "sin_mapeo": [],
}


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:120]


def decode_header_value(value) -> str:
    if value is None:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                decoded.append(part.decode("latin-1", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def es_procurador_autorizado(from_addr: str) -> bool:
    from_lower = from_addr.lower()
    for proc in PROCURADORES_AUTORIZADOS:
        if proc.lower() in from_lower:
            return True
    return False


def cargar_mapeo_expedientes() -> dict:
    """Carga el mapeo de expedientes desde JSON."""
    if not MAPEO_PATH.exists():
        log.warning(f"No se encuentra mapeo de expedientes: {MAPEO_PATH}")
        return {"por_procedimiento": {}, "por_asunto_regex": {}}
    with open(MAPEO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_mapeo_expedientes(mapeo: dict):
    """Guarda el mapeo actualizado."""
    with open(MAPEO_PATH, "w", encoding="utf-8") as f:
        json.dump(mapeo, f, ensure_ascii=False, indent=2)


def normalizar_procedimiento(proc: str) -> str:
    """Normaliza número de procedimiento: 000651-2023 -> 651/2023"""
    if not proc:
        return ""
    proc = proc.replace("-", "/").replace(" ", "")
    # Quitar ceros a la izquierda
    parts = proc.split("/")
    if len(parts) == 2:
        try:
            num = str(int(parts[0]))
            anho = parts[1]
            if len(anho) == 2:
                anho = "20" + anho
            return f"{num}/{anho}"
        except ValueError:
            pass
    return proc


def buscar_carpeta_usu2(nombre_cliente: str) -> Path | None:
    """Busca la carpeta del cliente en Usu2 con coincidencia flexible."""
    if not USU2_DIR.exists():
        return None
    # Búsqueda exacta
    exact = USU2_DIR / nombre_cliente
    if exact.exists():
        return exact
    # Búsqueda normalizada
    def norm(s):
        return s.lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n").replace(",","").strip()
    target = norm(nombre_cliente)
    for d in USU2_DIR.iterdir():
        if d.is_dir() and norm(d.name) == target:
            return d
    # Búsqueda parcial
    for d in USU2_DIR.iterdir():
        if d.is_dir() and target in norm(d.name):
            return d
    return None


# ─────────────────────────────────────────────
# PATRONES DE EXTRACCIÓN
# ─────────────────────────────────────────────
PATRONES = {
    "nig": [
        r'NIG[:\s]+([0-9]{4,5}\s*[\/\-]\s*[0-9]{4,6})',
        r'N\.I\.G\.?[:\s]+([0-9A-Z\-\/\s]{10,25})',
    ],
    "procedimiento": [
        r'(?:Procedimiento|Proc\.?|Autos?|Juicio)[:\s]+(?:Ordinario|Verbal|Monitorio|Ejecución|Divorcio|Concurso|Apelación|Recurso)?\s*(?:n[ºo°]?\.?\s*)?([0-9]+\s*[\/\-]\s*[0-9]{4})',
        r'(?:Rollo|Recurso)\s+(?:de\s+)?(?:Apelación|Casación)?\s*(?:n[ºo°]?\.?\s*)?([0-9]+\s*[\/\-]\s*[0-9]{4})',
        r'([0-9]{3,6}\s*[\/\-]\s*20[0-9]{2})',
    ],
    "tipo_resolucion": [
        r'\b(Auto(?:\s+de\s+[\w\s]+)?)\b',
        r'\b(Sentencia(?:\s+[\w\s]+)?)\b',
        r'\b(Providencia(?:\s+[\w\s]+)?)\b',
        r'\b(Decreto(?:\s+[\w\s]+)?)\b',
        r'\b(Diligencia(?:\s+de\s+[\w\s]+)?)\b',
        r'\b(Notificación(?:\s+[\w\s]+)?)\b',
        r'\b(Requerimiento(?:\s+[\w\s]+)?)\b',
    ],
    "partes": [
        r'(?:demandante[s]?|actor[a]?|ejecutante)[:\s]+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s,\.]{5,80}?)(?:\n|,)',
        r'(?:demandado[s]?|ejecutado[s]?)[:\s]+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s,\.]{5,80}?)(?:\n|,)',
    ],
}

SUBTIPOS_RESOLUCION = {
    r'auto.*tasaci[oó]n.*costas': 'Auto tasacion costas',
    r'auto.*admisi[oó]n': 'Auto admision',
    r'auto.*archivo': 'Auto archivo',
    r'auto.*ejecuci[oó]n': 'Auto ejecucion',
    r'diligencia.*negator': 'Dil negatoria prueba',
    r'diligencia.*requerimiento': 'Dil requerimiento',
    r'diligencia.*ordenaci[oó]n': 'Dil ordenacion',
    r'diligencia.*datos': 'Dil requerimiento datos',
    r'sentencia.*definitiva': 'Sentencia definitiva',
    r'providencia.*se[nñ]alamiento': 'Providencia senalamiento',
    r'vista|juicio': 'Senalamiento vista',
    r'requerimiento': 'Requerimiento',
    r'emplazamiento': 'Emplazamiento',
}


def extraer_texto_pdf(pdf_path: Path) -> str:
    """Extrae texto de un PDF usando pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber no instalado. Ejecute: pip install pdfplumber")
        return ""
    texto = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texto += t + "\n"
    except Exception as e:
        log.warning(f"Error extrayendo texto de {pdf_path.name}: {e}")
    return texto


def extraer_metadato(texto: str, campo: str) -> str:
    for patron in PATRONES.get(campo, []):
        match = re.search(patron, texto, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def detectar_tipo_resolucion(texto: str) -> str:
    texto_lower = texto.lower()
    for patron, nombre in SUBTIPOS_RESOLUCION.items():
        if re.search(patron, texto_lower):
            return nombre
    tipo = extraer_metadato(texto, "tipo_resolucion")
    if tipo:
        return tipo.split()[0].upper()[:20]
    return "NOTIFICACION"


def detectar_plazos_procesales(texto: str) -> list:
    plazos = []
    patrones_plazo = [
        r'(?:plazo\s+de\s+)(\d+)\s+(?:días?|meses?)[^\n]{0,100}',
        r'(?:recurso[s]?\s+de\s+)(?:apelación|casación|reposición|queja)[^\n]{0,80}',
        r'(?:vista|juicio|acto\s+de\s+juicio)[^\n]{0,100}',
        r'(?:señalado\s+para\s+el\s+día\s+)(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})[^\n]{0,80}',
    ]
    for patron in patrones_plazo:
        matches = re.findall(patron, texto, re.IGNORECASE)
        for m in matches:
            if isinstance(m, str) and len(m) > 3:
                plazos.append(m.strip())
    return list(set(plazos))[:5]


# ─────────────────────────────────────────────
# FASE 1: DESCARGA DE CORREOS
# ─────────────────────────────────────────────
# Ruta del archivo JSON de resultados de Gmail MCP (generado externamente por Manus)
# Flujo correcto:
# 1. Manus ejecuta: manus-mcp-cli tool call gmail_search_messages --server gmail --input '{"q": "from:solasortega.com...", "max_results": 100}'
# 2. Manus obtiene los thread IDs de procuradores del resultado
# 3. Manus ejecuta: manus-mcp-cli tool call gmail_read_threads --server gmail --input '{"thread_ids": [...], "include_full_messages": true}'
#    -> Esto descarga los adjuntos a /home/ubuntu/gmail-attachments/{msg_id}/{filename}
# 4. Manus copia el resultado a /tmp/gmail_threads_result.json
# 5. Este script lee el JSON y procesa los PDFs
GMAIL_MCP_RESULT_FILE = Path("/tmp/gmail_threads_result.json")

def search_gmail_mcp(query: str, max_results: int = 50) -> dict:
    """Lee el resultado de gmail_read_threads desde archivo temporal.
    
    El archivo debe ser generado previamente por Manus ejecutando:
    1. manus-mcp-cli tool call gmail_search_messages --server gmail --input '{"q": "from:solasortega.com...", "max_results": 100}'
    2. Extraer thread IDs de procuradores del resultado
    3. manus-mcp-cli tool call gmail_read_threads --server gmail --input '{"thread_ids": [...], "include_full_messages": true}'
    4. cp /tmp/manus-mcp/mcp_result_XXXX.json /tmp/gmail_threads_result.json
    """
    if not GMAIL_MCP_RESULT_FILE.exists():
        log.error(f"Archivo de resultados Gmail MCP no encontrado: {GMAIL_MCP_RESULT_FILE}")
        log.error("Ejecutar primero el flujo de descarga de Manus y copiar el resultado a /tmp/gmail_threads_result.json")
        return None
    
    try:
        with open(GMAIL_MCP_RESULT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not data.get("success"):
            log.error(f"Resultado Gmail MCP no exitoso: {data}")
            return None
        log.info(f"Resultado Gmail MCP leído desde {GMAIL_MCP_RESULT_FILE}")
        return data
    except Exception as e:
        log.error(f"Error leyendo resultado Gmail MCP: {e}")
        return None

def descargar_pdfs_imap(fecha_inicio: datetime, fecha_fin: datetime) -> list:
    """Conecta por Gmail MCP y descarga PDFs de procuradores."""
    log.info("=" * 60)
    log.info(f"FASE 1: Descarga Gmail MCP | {fecha_inicio.date()} -> {fecha_fin.date()}")
    log.info("=" * 60)

    fecha_str = f"{fecha_inicio.strftime('%Y-%m-%d')}_al_{fecha_fin.strftime('%Y-%m-%d')}"
    work_dir = TRABAJO_DIR / fecha_str
    work_dir.mkdir(parents=True, exist_ok=True)

    pdfs_descargados = []

    try:
        # Formatear fechas para la query de Gmail (YYYY/MM/DD)
        after_date = fecha_inicio.strftime("%Y/%m/%d")
        # Para 'before', sumamos un día a fecha_fin para incluirlo completo
        before_date = (fecha_fin + timedelta(days=1)).strftime("%Y/%m/%d")
        
        query = f"has:attachment after:{after_date} before:{before_date}"
        log.info(f"Query Gmail: {query}")
        
        res = search_gmail_mcp(query, 100)
        
        if not res or not res.get("success"):
            log.warning("No se encontraron mensajes o hubo un error en MCP.")
            return []

        # gmail_read_threads devuelve result como lista de listas de mensajes
        result = res.get("result", {})
        if isinstance(result, list):
            # Cada elemento es una lista de mensajes (un thread)
            threads_raw = result
        else:
            threads_raw = result.get("threads", [])
        
        log.info(f"Hilos encontrados: {len(threads_raw)}")
        
        # Normalizar: cada thread puede ser una lista de mensajes o un dict con 'messages'
        threads = []
        for t in threads_raw:
            if isinstance(t, list):
                threads.append({"messages": t})
            elif isinstance(t, dict):
                threads.append(t)
        
        # Contamos mensajes totales revisados
        total_msgs = sum(len(t.get("messages", [])) for t in threads)
        stats["correos_revisados"] = total_msgs

        for thread in threads:
            for msg in thread.get("messages", []):
                try:
                    headers = msg.get("pickedHeaders", {})
                    from_addr = headers.get("from", "")
                    subject = headers.get("subject", "Sin asunto")
                    
                    if not es_procurador_autorizado(from_addr):
                        continue

                    log.info(f"Correo procurador: {from_addr[:40]} | {subject[:60]}")

                    # Obtener fecha/hora
                    internal_date = msg.get("internalDate")
                    if internal_date:
                        dt = datetime.fromtimestamp(int(internal_date)/1000)
                        hora_str = dt.strftime("%Hh%M")
                    else:
                        hora_str = "00h00"

                    attachments = msg.get("pickedAttachments", [])
                    for att in attachments:
                        filename = att.get("filename", "")
                        if not filename.lower().endswith(".pdf"):
                            continue
                            
                        msg_id = msg.get("id")
                        # Ruta donde MCP guarda automáticamente los adjuntos
                        src_path = Path(f"/home/ubuntu/gmail-attachments/{msg_id}/{filename}")
                        
                        if src_path.exists():
                            # Copiar al directorio de trabajo
                            safe_filename = sanitize_filename(filename)
                            if not safe_filename.lower().endswith(".pdf"):
                                safe_filename += ".pdf"
                                
                            pdf_path = work_dir / safe_filename
                            counter = 1
                            while pdf_path.exists():
                                stem = Path(safe_filename).stem
                                pdf_path = work_dir / f"{stem}_{counter}.pdf"
                                counter += 1
                                
                            shutil.copy2(src_path, pdf_path)
                            
                            log.info(f"  PDF descargado: {pdf_path.name}")
                            stats["pdfs_descargados"] += 1
                            pdfs_descargados.append({
                                "path": pdf_path,
                                "from": from_addr,
                                "subject": subject,
                                "hora": hora_str,
                                "filename_original": filename,
                            })
                        else:
                            # El PDF no está en disco: el conector MCP solo descarga adjuntos
                            # cuando Manus ejecuta gmail_search_messages directamente.
                            # Para descargar este adjunto, usar gmail_get_attachment:
                            att_id = att.get("attachmentId", "")
                            log.warning(f"  PDF NO en disco: {filename} (msg_id={msg_id}, att_id={att_id[:20]}...)")
                            log.warning(f"  Ejecutar: manus-mcp-cli tool call gmail_get_attachment --server gmail --input '{{\"message_id\": \"{msg_id}\", \"attachment_id\": \"{att_id}\"}}'")

                except Exception as e:
                    log.error(f"Error procesando mensaje: {e}")
                    stats["errores"].append(str(e))

        log.info(f"Total PDFs descargados: {stats['pdfs_descargados']}")

    except Exception as e:
        log.error(f"Error Gmail MCP: {e}")
        stats["errores"].append(str(e))

    return pdfs_descargados


# ─────────────────────────────────────────────
# FASE 2: CLASIFICACIÓN
# ─────────────────────────────────────────────
def clasificar_pdf(info: dict) -> dict:
    """Clasifica un PDF judicial y extrae metadatos."""
    pdf_path = info["path"]
    texto = extraer_texto_pdf(pdf_path)

    procedimiento = extraer_metadato(texto, "procedimiento")
    nig = extraer_metadato(texto, "nig")
    tipo_resolucion = detectar_tipo_resolucion(texto)
    partes = extraer_metadato(texto, "partes")
    plazos = detectar_plazos_procesales(texto)

    vinculable = bool(procedimiento or nig)
    parte_principal = partes.split(",")[0].strip() if partes else ""
    parte_principal = re.sub(r'^(?:D\.?|Dña\.?|Don|Doña)\s+', '', parte_principal).strip()

    proc_filename = procedimiento.replace("/", "-").replace(" ", "") if procedimiento else "SIN-PROC"
    parte_corta = sanitize_filename(parte_principal[:25]) if parte_principal else "PARTE"
    nombre_descriptivo = f"{info['hora']} - {parte_corta} - {proc_filename} - {tipo_resolucion}.pdf"
    nombre_descriptivo = sanitize_filename(nombre_descriptivo)

    resultado = {
        **info,
        "texto_breve": texto[:1500],
        "nig": nig,
        "procedimiento": procedimiento,
        "proc_normalizado": normalizar_procedimiento(procedimiento),
        "tipo_resolucion": tipo_resolucion,
        "partes": partes,
        "parte_principal": parte_principal,
        "plazos": plazos,
        "vinculable": vinculable,
        "nombre_descriptivo": nombre_descriptivo,
    }

    log.info(f"  Clasificado: {nombre_descriptivo} | Proc: {procedimiento or 'N/A'}")
    stats["pdfs_clasificados"] += 1
    if vinculable:
        stats["pdfs_vinculables"] += 1

    return resultado


# ─────────────────────────────────────────────
# FASE 3: GUARDAR EN NOTIFICACIONES
# ─────────────────────────────────────────────
def guardar_en_notificaciones(pdfs_clasificados: list, fecha_str: str):
    """Guarda copias con nombres descriptivos en Notificaciones."""
    log.info("=" * 60)
    log.info("FASE 3: Guardando en Notificaciones")
    log.info("=" * 60)

    notif_dir = NOTIFICACIONES_DIR / fecha_str
    notif_dir.mkdir(parents=True, exist_ok=True)

    for pdf_info in pdfs_clasificados:
        try:
            src = pdf_info["path"]
            nombre_dest = pdf_info["nombre_descriptivo"]
            dest = notif_dir / nombre_dest

            counter = 1
            while dest.exists():
                stem = Path(nombre_dest).stem
                dest = notif_dir / f"{stem}_{counter}.pdf"
                counter += 1

            shutil.copy2(src, dest)
            log.info(f"  Guardado: {dest.name}")
            stats["pdfs_guardados_notificaciones"] += 1
            pdf_info["ruta_notificacion"] = str(dest)

        except Exception as e:
            log.error(f"Error guardando {pdf_info['path'].name}: {e}")
            stats["errores"].append(str(e))


# ─────────────────────────────────────────────
# FASE 4: CLASIFICAR EN USU2
# ─────────────────────────────────────────────
def clasificar_en_usu2(pdfs_clasificados: list, mapeo: dict):
    """Copia cada PDF a su carpeta de cliente en Usu2."""
    log.info("=" * 60)
    log.info("FASE 4: Clasificando en Usu2")
    log.info("=" * 60)

    for pdf_info in pdfs_clasificados:
        carpeta_cliente = None
        metodo = ""

        # 1. Buscar por número de procedimiento normalizado
        proc_norm = pdf_info.get("proc_normalizado", "")
        if proc_norm and proc_norm in mapeo.get("por_procedimiento", {}):
            carpeta_cliente = mapeo["por_procedimiento"][proc_norm]
            metodo = f"procedimiento {proc_norm}"

        # 2. Buscar por asunto del correo con regex
        if not carpeta_cliente:
            subject = pdf_info.get("subject", "")
            for regex, carpeta in mapeo.get("por_asunto_regex", {}).items():
                if re.search(regex, subject):
                    carpeta_cliente = carpeta
                    metodo = f"asunto regex"
                    break

        # 3. Buscar por parte principal en el nombre de las carpetas Usu2
        if not carpeta_cliente and pdf_info.get("parte_principal"):
            parte = pdf_info["parte_principal"]
            found = buscar_carpeta_usu2(parte)
            if found:
                carpeta_cliente = found.name
                metodo = f"parte '{parte}'"

        if carpeta_cliente:
            dest_dir = buscar_carpeta_usu2(carpeta_cliente)
            if dest_dir:
                src = Path(pdf_info.get("ruta_notificacion", pdf_info["path"]))
                dest_file = dest_dir / src.name
                counter = 1
                while dest_file.exists():
                    stem = src.stem
                    dest_file = dest_dir / f"{stem}_{counter}.pdf"
                    counter += 1
                try:
                    shutil.copy2(src, dest_file)
                    log.info(f"  Usu2 OK: {src.name} -> {dest_dir.name}/ ({metodo})")
                    stats["pdfs_copiados_usu2"] += 1
                    pdf_info["carpeta_usu2"] = dest_dir.name
                except Exception as e:
                    log.error(f"  Usu2 ERROR: {src.name} -> {e}")
                    stats["errores"].append(str(e))
            else:
                log.warning(f"  Usu2: Carpeta no encontrada '{carpeta_cliente}' para {pdf_info['nombre_descriptivo']}")
                stats["sin_mapeo"].append(pdf_info["nombre_descriptivo"])
                stats["pdfs_sin_mapeo"] += 1
        else:
            log.warning(f"  SIN MAPEO: {pdf_info['nombre_descriptivo']} (Proc: {proc_norm}, Asunto: {pdf_info.get('subject','')[:40]})")
            stats["sin_mapeo"].append(pdf_info["nombre_descriptivo"])
            stats["pdfs_sin_mapeo"] += 1


# ─────────────────────────────────────────────
# FASE 5: INFORME
# ─────────────────────────────────────────────
def generar_informe(pdfs_clasificados: list, fecha_str: str) -> Path:
    """Genera informe resumen en Markdown."""
    log.info("=" * 60)
    log.info("FASE 5: Generando informe")
    log.info("=" * 60)

    work_dir = TRABAJO_DIR / fecha_str
    work_dir.mkdir(parents=True, exist_ok=True)

    informe_path = work_dir / "informe_clasificacion.md"
    hoy = datetime.now()

    lineas = [
        "# Informe de Clasificación de Documentos Judiciales",
        f"**Autor:** Asesores y Abogados ASerges SL",
        f"**Fecha:** {hoy.strftime('%d/%m/%Y %H:%M')}",
        f"**Periodo:** {fecha_str}",
        "",
        "## Resumen",
        "",
        "| Métrica | Valor |",
        "|:---|---:|",
        f"| Correos revisados | {stats['correos_revisados']} |",
        f"| PDFs descargados | {stats['pdfs_descargados']} |",
        f"| PDFs clasificados | {stats['pdfs_clasificados']} |",
        f"| Guardados en Notificaciones | {stats['pdfs_guardados_notificaciones']} |",
        f"| Copiados a Usu2 | {stats['pdfs_copiados_usu2']} |",
        f"| Sin mapeo (pendientes) | {stats['pdfs_sin_mapeo']} |",
        f"| Errores | {len(stats['errores'])} |",
        "",
    ]

    # Documentos mapeados agrupados por carpeta
    por_carpeta = {}
    for pdf in pdfs_clasificados:
        carpeta = pdf.get("carpeta_usu2", "SIN ASIGNAR")
        por_carpeta.setdefault(carpeta, []).append(pdf)

    lineas.append("## Documentos Clasificados en Usu2")
    lineas.append("")
    for carpeta, docs in sorted(por_carpeta.items()):
        if carpeta == "SIN ASIGNAR":
            continue
        lineas.append(f"### {carpeta}")
        lineas.append("")
        lineas.append("| Archivo | Procedimiento | Tipo |")
        lineas.append("|:---|:---|:---|")
        for doc in docs:
            lineas.append(f"| {doc['nombre_descriptivo']} | {doc.get('proc_normalizado','N/A')} | {doc['tipo_resolucion']} |")
        lineas.append("")

    # Sin mapeo
    sin_asignar = por_carpeta.get("SIN ASIGNAR", [])
    if sin_asignar or stats["sin_mapeo"]:
        lineas.append("## Documentos NO Mapeados (Pendientes de Revisión)")
        lineas.append("")
        lineas.append("| Archivo | Procedimiento | Asunto Correo |")
        lineas.append("|:---|:---|:---|")
        for doc in sin_asignar:
            lineas.append(f"| {doc['nombre_descriptivo']} | {doc.get('proc_normalizado','N/A')} | {doc.get('subject','')[:50]} |")
        lineas.append("")

    # Errores
    if stats["errores"]:
        lineas.append("## Errores")
        lineas.append("")
        for err in stats["errores"]:
            lineas.append(f"- {err}")
        lineas.append("")

    # Plazos detectados
    plazos_docs = [(d, p) for d in pdfs_clasificados for p in d.get("plazos", []) if p]
    if plazos_docs:
        lineas.append("## Plazos Procesales Detectados")
        lineas.append("")
        lineas.append("| Documento | Plazo / Fecha |")
        lineas.append("|:---|:---|")
        for doc, plazo in plazos_docs:
            lineas.append(f"| {doc['nombre_descriptivo']} | {plazo} |")
        lineas.append("")

    with open(informe_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))

    # Guardar JSON de estadísticas
    stats_path = work_dir / "estadisticas.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in stats.items()}, f, ensure_ascii=False, indent=2, default=str)

    log.info(f"Informe: {informe_path}")
    return informe_path


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Pipeline Judicial Diario - ASERGES")
    parser.add_argument("--desde", type=str, help="Fecha inicio (YYYY-MM-DD). Por defecto: hoy.")
    parser.add_argument("--hasta", type=str, help="Fecha fin (YYYY-MM-DD). Por defecto: hoy.")
    args = parser.parse_args()

    hoy = datetime.now().replace(hour=23, minute=59, second=59)
    if args.desde:
        fecha_inicio = datetime.strptime(args.desde, "%Y-%m-%d")
    else:
        fecha_inicio = datetime.now().replace(hour=0, minute=0, second=0)
    if args.hasta:
        fecha_fin = datetime.strptime(args.hasta, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    else:
        fecha_fin = hoy

    fecha_str = f"{fecha_inicio.strftime('%Y-%m-%d')}_al_{fecha_fin.strftime('%Y-%m-%d')}"

    print("=" * 60)
    print("  PIPELINE JUDICIAL DIARIO - ASERGES")
    print(f"  Periodo: {fecha_str}")
    print("=" * 60)

    # Fase 1: Descargar
    pdfs = descargar_pdfs_imap(fecha_inicio, fecha_fin)

    # Fase 2: Clasificar
    clasificados = []
    if pdfs:
        for info in pdfs:
            try:
                clasificados.append(clasificar_pdf(info))
            except Exception as e:
                log.error(f"Error clasificando: {e}")
                stats["errores"].append(str(e))

    # Fase 3: Guardar en Notificaciones
    if clasificados:
        guardar_en_notificaciones(clasificados, fecha_str)

    # Fase 4: Clasificar en Usu2
    if clasificados:
        mapeo = cargar_mapeo_expedientes()
        clasificar_en_usu2(clasificados, mapeo)

    # Fase 5: Informe
    informe = generar_informe(clasificados, fecha_str)

    # Resumen en consola
    print("\n" + "=" * 60)
    print("  RESUMEN")
    print("=" * 60)
    print(f"  Correos revisados:        {stats['correos_revisados']}")
    print(f"  PDFs descargados:         {stats['pdfs_descargados']}")
    print(f"  Guardados Notificaciones: {stats['pdfs_guardados_notificaciones']}")
    print(f"  Copiados a Usu2:          {stats['pdfs_copiados_usu2']}")
    print(f"  Sin mapeo:                {stats['pdfs_sin_mapeo']}")
    print(f"  Errores:                  {len(stats['errores'])}")
    if stats["sin_mapeo"]:
        print("\n  ARCHIVOS SIN MAPEO:")
        for f in stats["sin_mapeo"]:
            print(f"    - {f}")
    print("=" * 60)
    print(f"  Informe: {informe}")
    print("=" * 60)


if __name__ == "__main__":
    main()
