#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Judicial Diario - ASERGES  v2.0
=========================================
Descarga correos de procuradores desde Gmail (MCP gmail),
clasifica PDFs judiciales con regex + LLM fallback,
los copia a Notificaciones y a Usu2 (con deduplicación por hash),
crea eventos en Google Calendar para plazos procesales
y envía un correo HTML de resumen.

MEJORAS v2.0:
  - Extracción de metadatos con LLM (gpt-4.1-mini) como fallback a regex
  - Normalización robusta de procedimientos (MON 686-25 -> 686/2025)
  - Deduplicación de archivos por hash SHA-256 (evita copias duplicadas)
  - Nomenclatura descriptiva mejorada y consistente
  - Mapeo dinámico: los expedientes sin mapeo se registran en pending_mapeo.json
  - Integración con Google Calendar para plazos procesales (MCP google-calendar)
  - Correo HTML de resumen con tabla de documentos y plazos (MCP gmail)

Uso:
  python pipeline_judicial.py                    # Procesa correos de hoy
  python pipeline_judicial.py --desde 2026-03-16 # Desde fecha hasta hoy
  python pipeline_judicial.py --desde 2026-03-16 --hasta 2026-03-21
  python pipeline_judicial.py --no-calendar      # Sin crear eventos en Calendar
  python pipeline_judicial.py --no-email         # Sin enviar correo de resumen
"""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
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

PROCURADORES_AUTORIZADOS = CFG["procuradores_autorizados"]
RUTAS = CFG["rutas_windows"]
NOTIFICACIONES_DIR = Path(RUTAS["notificaciones"])
USU2_DIR = Path(RUTAS["usu2"])
TRABAJO_DIR = Path(RUTAS["trabajo"])
MAPEO_PATH = SCRIPT_DIR / CFG.get("mapeo_expedientes_archivo", "mapeos/mapeo_expedientes.json")
PENDING_MAPEO_PATH = SCRIPT_DIR / "mapeos" / "pending_mapeo.json"

# LLM
USAR_LLM = CFG.get("usar_llm_para_clasificar", True)
MODELO_LLM = CFG.get("modelo_llm", "gpt-4.1-mini")
OPENAI_API_KEY = os.environ.get(CFG.get("openai_api_key_env", "OPENAI_API_KEY"), "")

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
    "pdfs_duplicados_omitidos": 0,
    "pdfs_sin_mapeo": 0,
    "eventos_calendar_creados": 0,
    "errores": [],
    "documentos": [],
    "sin_mapeo": [],
    "plazos_calendar": [],
}


# ─────────────────────────────────────────────
# UTILIDADES GENERALES
# ─────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:120]


def sha256_file(path: Path) -> str:
    """Calcula el hash SHA-256 de un archivo para deduplicación."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
    MAPEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MAPEO_PATH, "w", encoding="utf-8") as f:
        json.dump(mapeo, f, ensure_ascii=False, indent=2)


def registrar_pending_mapeo(proc_norm: str, texto_breve: str, nombre_archivo: str):
    """Registra expedientes sin mapeo en pending_mapeo.json para revisión posterior."""
    if not proc_norm:
        return
    PENDING_MAPEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    pending = {}
    if PENDING_MAPEO_PATH.exists():
        with open(PENDING_MAPEO_PATH, "r", encoding="utf-8") as f:
            pending = json.load(f)
    if proc_norm not in pending:
        pending[proc_norm] = {
            "primera_deteccion": datetime.now().isoformat(),
            "archivos": [],
            "texto_muestra": texto_breve[:300],
            "carpeta_usu2": "",  # Rellenar manualmente
        }
    if nombre_archivo not in pending[proc_norm]["archivos"]:
        pending[proc_norm]["archivos"].append(nombre_archivo)
    with open(PENDING_MAPEO_PATH, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def normalizar_procedimiento(proc: str) -> str:
    """Normaliza número de procedimiento a formato NUM/AÑO.

    Maneja formatos como:
      - 000651-2023  -> 651/2023
      - MON 686-25   -> 686/2025
      - 686/2025     -> 686/2025
      - 1132/2025    -> 1132/2025
      - 0000686/2025 -> 686/2025
    """
    if not proc:
        return ""
    # Eliminar prefijos de tipo de procedimiento (MON, ENJ, EJH, CNO, DPA, etc.)
    proc = re.sub(r'^[A-Z]{2,4}\s+', '', proc.strip())
    proc = proc.replace("-", "/").replace(" ", "")
    parts = proc.split("/")
    if len(parts) == 2:
        try:
            num = str(int(parts[0]))
            anho = parts[1]
            if len(anho) == 2:
                anho = "20" + anho
            elif len(anho) == 4 and anho.isdigit():
                pass  # ya está bien
            return f"{num}/{anho}"
        except ValueError:
            pass
    return proc


def buscar_carpeta_usu2(nombre_cliente: str) -> Path | None:
    """Busca la carpeta del cliente en Usu2 con coincidencia flexible."""
    if not USU2_DIR.exists():
        return None

    def norm(s):
        return (s.lower()
                .replace("á", "a").replace("é", "e").replace("í", "i")
                .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
                .replace(",", "").replace(".", "").strip())

    exact = USU2_DIR / nombre_cliente
    if exact.exists():
        return exact
    target = norm(nombre_cliente)
    for d in USU2_DIR.iterdir():
        if d.is_dir() and norm(d.name) == target:
            return d
    for d in USU2_DIR.iterdir():
        if d.is_dir() and target in norm(d.name):
            return d
    return None


# ─────────────────────────────────────────────
# PATRONES DE EXTRACCIÓN (MEJORADOS)
# ─────────────────────────────────────────────
PATRONES = {
    "nig": [
        r'NIG[:\s]+([0-9]{4,5}\s*[\/\-]\s*[0-9]{4,6})',
        r'N\.I\.G\.?[:\s]+([0-9A-Z\-\/\s]{10,25})',
        r'NIG\s+([0-9]{13,20})',  # NIG largo sin separadores
    ],
    "procedimiento": [
        # Formato explícito con tipo: "Procedimiento: Monitorio 686/2025"
        r'(?:Procedimiento|Proc\.?|Autos?|Juicio)[:\s]+(?:Ordinario|Verbal|Monitorio|Ejecuci[oó]n|Divorcio|Concurso|Apelaci[oó]n|Recurso|Ejecutivo|Hipotecario|Penal|Instruccion|Diligencias)?\s*(?:n[ºo°]?\.?\s*)?([0-9]+\s*[\/\-]\s*[0-9]{2,4})',
        # Rollo de apelación
        r'(?:Rollo|Recurso)\s+(?:de\s+)?(?:Apelaci[oó]n|Casaci[oó]n)?\s*(?:n[ºo°]?\.?\s*)?([0-9]+\s*[\/\-]\s*[0-9]{4})',
        # Tipo abreviado con guión: "MON 686-25", "ENJ 197-17"
        r'\b(?:MON|ENJ|EJH|CNO|DPA|ORD|VRB|DIV|APE|REC|EJE|HIP)\s+([0-9]+[\/\-][0-9]{2,4})\b',
        # Número suelto con año de 4 dígitos: "686/2025", "0000686/2025"
        r'\b([0-9]{1,6}\s*[\/\-]\s*20[0-9]{2})\b',
    ],
    "tipo_resolucion": [
        r'\b(Auto(?:\s+de\s+[\w\s]+)?)\b',
        r'\b(Sentencia(?:\s+[\w\s]+)?)\b',
        r'\b(Providencia(?:\s+[\w\s]+)?)\b',
        r'\b(Decreto(?:\s+[\w\s]+)?)\b',
        r'\b(Diligencia(?:\s+de\s+[\w\s]+)?)\b',
        r'\b(Notificaci[oó]n(?:\s+[\w\s]+)?)\b',
        r'\b(Requerimiento(?:\s+[\w\s]+)?)\b',
    ],
    "partes": [
        r'(?:demandante[s]?|actor[a]?|ejecutante)[:\s]+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s,\.]{5,80}?)(?:\n|,)',
        r'(?:demandado[s]?|ejecutado[s]?)[:\s]+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\s,\.]{5,80}?)(?:\n|,)',
        # LexNET: "en nombre y representación de RIOJASTUR CALIDAD, S.L."
        r'representaci[oó]n\s+de\s+(?:la\s+mercantil\s+|el\s+)?([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñÁÉÍÓÚÑ\s,\.]{5,60}?)(?:,|\s+con\s+CIF)',
    ],
    "fecha_vista": [
        r'se[nñ]alado\s+para\s+el\s+d[ií]a\s+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
        r'para\s+el\s+d[ií]a\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})',
        r'celebraci[oó]n\s+(?:del\s+)?(?:juicio|vista)[^\n]{0,60}(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})',
    ],
}

SUBTIPOS_RESOLUCION = {
    r'auto.*tasaci[oó]n.*costas': 'Auto tasacion costas',
    r'auto.*admisi[oó]n': 'Auto admision',
    r'auto.*archivo': 'Auto archivo',
    r'auto.*ejecuci[oó]n': 'Auto ejecucion',
    r'auto.*declaraci[oó]n.*concurso': 'Auto declaracion concurso',
    r'auto.*conclusi[oó]n': 'Auto conclusion concurso',
    r'auto.*apertura': 'Auto apertura fase',
    r'diligencia.*negator': 'Dil negatoria prueba',
    r'diligencia.*requerimiento': 'Dil requerimiento',
    r'diligencia.*ordenaci[oó]n': 'Dil ordenacion',
    r'diligencia.*datos': 'Dil requerimiento datos',
    r'diligencia.*embargo': 'Dil embargo',
    r'sentencia.*definitiva': 'Sentencia definitiva',
    r'sentencia': 'Sentencia',
    r'providencia.*se[nñ]alamiento': 'Providencia senalamiento',
    r'providencia': 'Providencia',
    r'decreto.*admisi[oó]n': 'Decreto admision',
    r'decreto': 'Decreto',
    r'vista|juicio': 'Senalamiento vista',
    r'requerimiento': 'Requerimiento',
    r'emplazamiento': 'Emplazamiento',
    r'acuse\s+de\s+presentaci[oó]n|acuse\s+de\s+recibo': 'Acuse presentacion',
    r'testimonio.*firmeza': 'Testimonio firmeza',
    r'oficio': 'Oficio',
}


def extraer_texto_pdf(pdf_path: Path) -> str:
    """Extrae texto de un PDF usando pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber no instalado. Ejecute: sudo pip3 install pdfplumber")
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
    """Extrae un metadato del texto usando los patrones definidos."""
    for patron in PATRONES.get(campo, []):
        match = re.search(patron, texto, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def extraer_metadatos_llm(texto: str, filename_original: str, subject: str) -> dict:
    """Usa LLM para extraer metadatos cuando los regex fallan.

    Retorna dict con claves: procedimiento, nig, tipo_resolucion, partes, plazos_texto
    """
    if not USAR_LLM or not OPENAI_API_KEY:
        return {}
    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = f"""Eres un asistente jurídico especializado en documentos judiciales españoles.
Analiza el siguiente texto extraído de un PDF judicial y extrae estos metadatos en JSON:
- procedimiento: número de procedimiento en formato NUM/AÑO (ej: 686/2025). Si hay prefijo de tipo (MON, ENJ, etc.), ignóralo.
- nig: Número de Identificación General (NIG) si aparece.
- tipo_resolucion: tipo de resolución (Auto, Sentencia, Providencia, Decreto, Diligencia de Ordenación, Requerimiento, Emplazamiento, Acuse de Presentación, etc.)
- partes: nombre del cliente/parte representada (demandante o demandado principal).
- plazos: lista de plazos o fechas relevantes detectados (vistas, recursos, requerimientos). Formato: ["plazo o fecha concreta"].
- fecha_vista: fecha concreta de vista o juicio si aparece (formato DD/MM/YYYY).

Nombre del archivo: {filename_original}
Asunto del correo: {subject}

Texto del PDF (primeros 2000 caracteres):
{texto[:2000]}

Responde SOLO con JSON válido, sin explicaciones."""

        response = client.chat.completions.create(
            model=MODELO_LLM,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()
        # Limpiar posibles bloques markdown
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        log.warning(f"LLM fallback falló: {e}")
        return {}


def detectar_tipo_resolucion(texto: str) -> str:
    """Detecta el tipo de resolución judicial en el texto."""
    texto_lower = texto.lower()
    for patron, nombre in SUBTIPOS_RESOLUCION.items():
        if re.search(patron, texto_lower):
            return nombre
    tipo = extraer_metadato(texto, "tipo_resolucion")
    if tipo:
        return tipo.split()[0].upper()[:20]
    return "NOTIFICACION"


def detectar_plazos_procesales(texto: str) -> list:
    """Detecta plazos procesales y fechas relevantes en el texto."""
    plazos = []
    patrones_plazo = [
        r'(?:plazo\s+de\s+)(\d+)\s+(?:d[ií]as?|meses?)[^\n]{0,100}',
        r'(?:recurso[s]?\s+de\s+)(?:apelaci[oó]n|casaci[oó]n|reposici[oó]n|queja)[^\n]{0,80}',
        r'(?:vista|juicio|acto\s+de\s+juicio)[^\n]{0,100}',
        r'(?:se[nñ]alado\s+para\s+el\s+d[ií]a\s+)(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})[^\n]{0,80}',
        r'en\s+el\s+plazo\s+de\s+(\d+)\s+(?:d[ií]as?|meses?)[^\n]{0,80}',
    ]
    for patron in patrones_plazo:
        matches = re.findall(patron, texto, re.IGNORECASE)
        for m in matches:
            if isinstance(m, str) and len(m) > 3:
                plazos.append(m.strip())
    return list(set(plazos))[:5]


def parsear_fecha_vista(texto_fecha: str) -> datetime | None:
    """Intenta parsear una fecha de vista en varios formatos."""
    formatos = [
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%d de %B de %Y",
    ]
    meses_es = {
        "enero": "January", "febrero": "February", "marzo": "March",
        "abril": "April", "mayo": "May", "junio": "June",
        "julio": "July", "agosto": "August", "septiembre": "September",
        "octubre": "October", "noviembre": "November", "diciembre": "December",
    }
    texto_en = texto_fecha.lower()
    for es, en in meses_es.items():
        texto_en = texto_en.replace(es, en)
    for fmt in formatos:
        try:
            return datetime.strptime(texto_en.strip(), fmt)
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────
# FASE 1: DESCARGA DE CORREOS (Gmail MCP)
# ─────────────────────────────────────────────
GMAIL_MCP_RESULT_FILE = Path("/tmp/gmail_threads_result.json")


def search_gmail_mcp(query: str, max_results: int = 50) -> dict:
    """Lee el resultado de gmail_read_threads desde archivo temporal.

    El archivo debe ser generado previamente por Manus ejecutando:
    1. manus-mcp-cli tool call gmail_search_messages --server gmail
       --input '{"q": "from:solasortega.com OR from:procuradoracarmencarrasco@gmail.com has:attachment", "max_results": 100}'
    2. Extraer thread IDs de procuradores del resultado
    3. manus-mcp-cli tool call gmail_read_threads --server gmail
       --input '{"thread_ids": [...], "include_full_messages": true}'
       -> Esto descarga los adjuntos a /home/ubuntu/gmail-attachments/{msg_id}/{filename}
    4. cp /tmp/manus-mcp/mcp_result_XXXX.json /tmp/gmail_threads_result.json
    """
    if not GMAIL_MCP_RESULT_FILE.exists():
        log.error(f"Archivo de resultados Gmail MCP no encontrado: {GMAIL_MCP_RESULT_FILE}")
        log.error("Ejecutar primero el flujo de descarga de Manus y copiar el resultado a /tmp/gmail_threads_result.json")
        return None

    try:
        with open(GMAIL_MCP_RESULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("success"):
            log.error(f"Resultado Gmail MCP no exitoso: {data}")
            return None
        log.info(f"Resultado Gmail MCP leído desde {GMAIL_MCP_RESULT_FILE}")
        return data
    except Exception as e:
        log.error(f"Error leyendo resultado Gmail MCP: {e}")
        return None


def descargar_pdfs_gmail(fecha_inicio: datetime, fecha_fin: datetime) -> list:
    """Lee PDFs de procuradores desde los adjuntos descargados por Gmail MCP."""
    log.info("=" * 60)
    log.info(f"FASE 1: Descarga Gmail MCP | {fecha_inicio.date()} -> {fecha_fin.date()}")
    log.info("=" * 60)

    fecha_str = f"{fecha_inicio.strftime('%Y-%m-%d')}_al_{fecha_fin.strftime('%Y-%m-%d')}"
    work_dir = TRABAJO_DIR / fecha_str
    work_dir.mkdir(parents=True, exist_ok=True)

    pdfs_descargados = []
    # Registro de hashes para deduplicación en descarga
    hashes_vistos = set()

    try:
        after_date = fecha_inicio.strftime("%Y/%m/%d")
        before_date = (fecha_fin + timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"has:attachment after:{after_date} before:{before_date}"
        log.info(f"Query Gmail: {query}")

        res = search_gmail_mcp(query, 100)
        if not res or not res.get("success"):
            log.warning("No se encontraron mensajes o hubo un error en MCP.")
            return []

        result = res.get("result", {})
        threads_raw = result if isinstance(result, list) else result.get("threads", [])
        log.info(f"Hilos encontrados: {len(threads_raw)}")

        threads = []
        for t in threads_raw:
            if isinstance(t, list):
                threads.append({"messages": t})
            elif isinstance(t, dict):
                threads.append(t)

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

                    internal_date = msg.get("internalDate")
                    if internal_date:
                        dt = datetime.fromtimestamp(int(internal_date) / 1000)
                        hora_str = dt.strftime("%Hh%M")
                        fecha_msg = dt.date()
                    else:
                        hora_str = "00h00"
                        fecha_msg = fecha_inicio.date()

                    # Filtrar por rango de fechas
                    if not (fecha_inicio.date() <= fecha_msg <= fecha_fin.date()):
                        continue

                    attachments = msg.get("pickedAttachments", [])
                    for att in attachments:
                        filename = att.get("filename", "")
                        if not filename.lower().endswith(".pdf"):
                            continue

                        msg_id = msg.get("id")
                        src_path = Path(f"/home/ubuntu/gmail-attachments/{msg_id}/{filename}")

                        if not src_path.exists():
                            att_id = att.get("attachmentId", "")
                            log.warning(f"  PDF NO en disco: {filename} (msg_id={msg_id})")
                            continue

                        # Deduplicación por hash SHA-256
                        file_hash = sha256_file(src_path)
                        if file_hash in hashes_vistos:
                            log.info(f"  DUPLICADO omitido: {filename}")
                            stats["pdfs_duplicados_omitidos"] += 1
                            continue
                        hashes_vistos.add(file_hash)

                        safe_filename = sanitize_filename(filename)
                        if not safe_filename.lower().endswith(".pdf"):
                            safe_filename += ".pdf"

                        pdf_path = work_dir / safe_filename
                        counter = 1
                        while pdf_path.exists():
                            # Verificar si es el mismo archivo (por hash)
                            if sha256_file(pdf_path) == file_hash:
                                log.info(f"  Ya existe (mismo contenido): {pdf_path.name}")
                                stats["pdfs_duplicados_omitidos"] += 1
                                pdf_path = None
                                break
                            stem = Path(safe_filename).stem
                            pdf_path = work_dir / f"{stem}_{counter}.pdf"
                            counter += 1

                        if pdf_path is None:
                            continue

                        shutil.copy2(src_path, pdf_path)
                        log.info(f"  PDF descargado: {pdf_path.name}")
                        stats["pdfs_descargados"] += 1
                        pdfs_descargados.append({
                            "path": pdf_path,
                            "from": from_addr,
                            "subject": subject,
                            "hora": hora_str,
                            "fecha_msg": str(fecha_msg),
                            "filename_original": filename,
                            "hash": file_hash,
                        })

                except Exception as e:
                    log.error(f"Error procesando mensaje: {e}")
                    stats["errores"].append(str(e))

        log.info(f"Total PDFs descargados: {stats['pdfs_descargados']} | Duplicados omitidos: {stats['pdfs_duplicados_omitidos']}")

    except Exception as e:
        log.error(f"Error Gmail MCP: {e}")
        stats["errores"].append(str(e))

    return pdfs_descargados


# ─────────────────────────────────────────────
# FASE 2: CLASIFICACIÓN (regex + LLM fallback)
# ─────────────────────────────────────────────
def clasificar_pdf(info: dict) -> dict:
    """Clasifica un PDF judicial extrayendo metadatos con regex + LLM fallback."""
    pdf_path = info["path"]
    texto = extraer_texto_pdf(pdf_path)

    # Extracción con regex
    procedimiento = extraer_metadato(texto, "procedimiento")
    nig = extraer_metadato(texto, "nig")
    tipo_resolucion = detectar_tipo_resolucion(texto)
    partes = extraer_metadato(texto, "partes")
    plazos = detectar_plazos_procesales(texto)
    fecha_vista_raw = extraer_metadato(texto, "fecha_vista")
    fecha_vista_dt = parsear_fecha_vista(fecha_vista_raw) if fecha_vista_raw else None

    # LLM fallback si los regex no encontraron procedimiento o partes
    llm_data = {}
    if USAR_LLM and (not procedimiento or not partes):
        log.info(f"  Usando LLM fallback para: {pdf_path.name}")
        llm_data = extraer_metadatos_llm(texto, info.get("filename_original", ""), info.get("subject", ""))
        if llm_data:
            if not procedimiento and llm_data.get("procedimiento"):
                procedimiento = llm_data["procedimiento"]
                log.info(f"    LLM -> procedimiento: {procedimiento}")
            if not nig and llm_data.get("nig"):
                nig = llm_data["nig"]
            if tipo_resolucion == "NOTIFICACION" and llm_data.get("tipo_resolucion"):
                tipo_resolucion = llm_data["tipo_resolucion"]
            if not partes and llm_data.get("partes"):
                partes = llm_data["partes"]
                log.info(f"    LLM -> partes: {partes}")
            if not plazos and llm_data.get("plazos"):
                plazos = llm_data["plazos"]
            if not fecha_vista_dt and llm_data.get("fecha_vista"):
                fecha_vista_dt = parsear_fecha_vista(llm_data["fecha_vista"])

    proc_norm = normalizar_procedimiento(procedimiento)
    vinculable = bool(proc_norm or nig)

    parte_principal = partes.split(",")[0].strip() if partes else ""
    parte_principal = re.sub(r'^(?:D\.?|Dña\.?|Don|Doña)\s+', '', parte_principal).strip()

    # Nomenclatura descriptiva mejorada: FECHA_HORA - CLIENTE - PROC - TIPO.pdf
    proc_filename = proc_norm.replace("/", "-") if proc_norm else "SIN-PROC"
    parte_corta = sanitize_filename(parte_principal[:25]) if parte_principal else "PARTE"
    tipo_corto = sanitize_filename(tipo_resolucion[:30])
    nombre_descriptivo = f"{info['hora']} - {parte_corta} - {proc_filename} - {tipo_corto}.pdf"
    nombre_descriptivo = sanitize_filename(nombre_descriptivo)

    resultado = {
        **info,
        "texto_breve": texto[:2000],
        "nig": nig,
        "procedimiento": procedimiento,
        "proc_normalizado": proc_norm,
        "tipo_resolucion": tipo_resolucion,
        "partes": partes,
        "parte_principal": parte_principal,
        "plazos": plazos,
        "fecha_vista": str(fecha_vista_dt.date()) if fecha_vista_dt else "",
        "fecha_vista_dt": fecha_vista_dt,
        "vinculable": vinculable,
        "nombre_descriptivo": nombre_descriptivo,
        "llm_usado": bool(llm_data),
    }

    log.info(f"  Clasificado: {nombre_descriptivo} | Proc: {proc_norm or 'N/A'} | NIG: {nig or 'N/A'}")
    stats["pdfs_clasificados"] += 1
    if vinculable:
        stats["pdfs_vinculables"] += 1

    return resultado


# ─────────────────────────────────────────────
# FASE 3: GUARDAR EN NOTIFICACIONES
# ─────────────────────────────────────────────
def guardar_en_notificaciones(pdfs_clasificados: list, fecha_str: str):
    """Guarda copias con nombres descriptivos en Notificaciones (con deduplicación por hash)."""
    log.info("=" * 60)
    log.info("FASE 3: Guardando en Notificaciones")
    log.info("=" * 60)

    notif_dir = NOTIFICACIONES_DIR / fecha_str
    notif_dir.mkdir(parents=True, exist_ok=True)

    # Índice de hashes ya guardados en Notificaciones
    hashes_notif = {}
    for f in notif_dir.glob("*.pdf"):
        try:
            hashes_notif[sha256_file(f)] = f
        except Exception:
            pass

    for pdf_info in pdfs_clasificados:
        try:
            src = pdf_info["path"]
            file_hash = pdf_info.get("hash") or sha256_file(src)

            # Deduplicación: si ya existe el mismo contenido, no copiar
            if file_hash in hashes_notif:
                log.info(f"  Ya en Notificaciones (mismo contenido): {hashes_notif[file_hash].name}")
                pdf_info["ruta_notificacion"] = str(hashes_notif[file_hash])
                continue

            nombre_dest = pdf_info["nombre_descriptivo"]
            dest = notif_dir / nombre_dest
            counter = 1
            while dest.exists():
                stem = Path(nombre_dest).stem
                dest = notif_dir / f"{stem}_{counter}.pdf"
                counter += 1

            shutil.copy2(src, dest)
            hashes_notif[file_hash] = dest
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
    """Copia cada PDF a su carpeta de cliente en Usu2 (con deduplicación por hash)."""
    log.info("=" * 60)
    log.info("FASE 4: Clasificando en Usu2")
    log.info("=" * 60)

    for pdf_info in pdfs_clasificados:
        carpeta_cliente = None
        metodo = ""

        # 1. Por número de procedimiento normalizado
        proc_norm = pdf_info.get("proc_normalizado", "")
        if proc_norm and proc_norm in mapeo.get("por_procedimiento", {}):
            carpeta_cliente = mapeo["por_procedimiento"][proc_norm]
            metodo = f"procedimiento {proc_norm}"

        # 2. Por NIG (si hay mapeo por NIG)
        if not carpeta_cliente:
            nig = pdf_info.get("nig", "")
            if nig and nig in mapeo.get("por_nig", {}):
                carpeta_cliente = mapeo["por_nig"][nig]
                metodo = f"NIG {nig}"

        # 3. Por asunto del correo con regex
        if not carpeta_cliente:
            subject = pdf_info.get("subject", "")
            for regex, carpeta in mapeo.get("por_asunto_regex", {}).items():
                if re.search(regex, subject, re.IGNORECASE):
                    carpeta_cliente = carpeta
                    metodo = "asunto regex"
                    break

        # 4. Por parte principal en el nombre de las carpetas Usu2
        if not carpeta_cliente and pdf_info.get("parte_principal"):
            parte = pdf_info["parte_principal"]
            found = buscar_carpeta_usu2(parte)
            if found:
                carpeta_cliente = found.name
                metodo = f"parte '{parte}'"

        if carpeta_cliente:
            dest_dir = buscar_carpeta_usu2(carpeta_cliente)
            if dest_dir:
                src = Path(pdf_info.get("ruta_notificacion", str(pdf_info["path"])))
                file_hash = pdf_info.get("hash") or sha256_file(src)

                # Deduplicación en Usu2
                ya_existe = False
                for f in dest_dir.glob("*.pdf"):
                    try:
                        if sha256_file(f) == file_hash:
                            log.info(f"  Usu2 DUPLICADO omitido: {src.name} ya existe como {f.name}")
                            ya_existe = True
                            pdf_info["carpeta_usu2"] = dest_dir.name
                            break
                    except Exception:
                        pass
                if ya_existe:
                    continue

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
                registrar_pending_mapeo(proc_norm, pdf_info.get("texto_breve", ""), pdf_info["nombre_descriptivo"])
        else:
            log.warning(f"  SIN MAPEO: {pdf_info['nombre_descriptivo']} (Proc: {proc_norm})")
            stats["sin_mapeo"].append(pdf_info["nombre_descriptivo"])
            stats["pdfs_sin_mapeo"] += 1
            registrar_pending_mapeo(proc_norm, pdf_info.get("texto_breve", ""), pdf_info["nombre_descriptivo"])


# ─────────────────────────────────────────────
# FASE 5: GOOGLE CALENDAR (plazos procesales)
# ─────────────────────────────────────────────
def crear_eventos_calendar(pdfs_clasificados: list):
    """Crea eventos en Google Calendar para plazos y vistas detectados."""
    log.info("=" * 60)
    log.info("FASE 5: Creando eventos en Google Calendar")
    log.info("=" * 60)

    for pdf_info in pdfs_clasificados:
        # Evento para fecha de vista/juicio concreta
        fecha_vista_dt = pdf_info.get("fecha_vista_dt")
        if fecha_vista_dt:
            try:
                titulo = f"VISTA: {pdf_info.get('proc_normalizado', 'S/N')} - {pdf_info.get('parte_principal', 'PARTE')}"
                descripcion = (
                    f"Procedimiento: {pdf_info.get('proc_normalizado', 'N/A')}\n"
                    f"Tipo: {pdf_info.get('tipo_resolucion', 'N/A')}\n"
                    f"Documento: {pdf_info['nombre_descriptivo']}\n"
                    f"Procurador: {pdf_info.get('from', '')}"
                )
                # Evento el día de la vista a las 09:00
                start_dt = fecha_vista_dt.replace(hour=9, minute=0, second=0)
                end_dt = start_dt + timedelta(hours=1)

                tz_offset = "+02:00"
                input_json = json.dumps({
                    "events": [{
                        "summary": titulo,
                        "description": descripcion,
                        "start_time": start_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}"),
                        "end_time": end_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}"),
                        "reminders": [1440],
                        "calendar_id": "primary",
                    }]
                }, ensure_ascii=False)

                result = subprocess.run(
                    ["manus-mcp-cli", "tool", "call", "google_calendar_create_events",
                     "--server", "google-calendar", "--input", input_json],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    log.info(f"  Calendar OK: {titulo} ({fecha_vista_dt.date()})")
                    stats["eventos_calendar_creados"] += 1
                    stats["plazos_calendar"].append({
                        "titulo": titulo,
                        "fecha": str(fecha_vista_dt.date()),
                        "tipo": "vista",
                    })
                else:
                    log.warning(f"  Calendar ERROR: {result.stderr[:100]}")
            except Exception as e:
                log.warning(f"  Calendar excepción: {e}")

        # Eventos para plazos procesales (días de vencimiento)
        for plazo_texto in pdf_info.get("plazos", []):
            # Intentar extraer número de días del texto
            match_dias = re.search(r'(\d+)\s+d[ií]as?', plazo_texto, re.IGNORECASE)
            if match_dias:
                try:
                    n_dias = int(match_dias.group(1))
                    # El plazo empieza desde la fecha del correo
                    fecha_inicio_plazo = datetime.strptime(pdf_info.get("fecha_msg", str(datetime.now().date())), "%Y-%m-%d")
                    fecha_vencimiento = fecha_inicio_plazo + timedelta(days=n_dias)
                    titulo = f"PLAZO {n_dias}d: {pdf_info.get('proc_normalizado', 'S/N')} - {pdf_info.get('tipo_resolucion', '')}"
                    descripcion = (
                        f"Plazo detectado: {plazo_texto[:200]}\n"
                        f"Procedimiento: {pdf_info.get('proc_normalizado', 'N/A')}\n"
                        f"Documento: {pdf_info['nombre_descriptivo']}"
                    )
                    # Evento el día de vencimiento a las 08:00
                    start_dt = fecha_vencimiento.replace(hour=8, minute=0, second=0)
                    end_dt = start_dt + timedelta(hours=1)

                    tz_offset = "+02:00"
                    input_json = json.dumps({
                        "events": [{
                            "summary": titulo,
                            "description": descripcion,
                            "start_time": start_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}"),
                            "end_time": end_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz_offset}"),
                            "reminders": [1440],
                            "calendar_id": "primary",
                        }]
                    }, ensure_ascii=False)

                    result = subprocess.run(
                        ["manus-mcp-cli", "tool", "call", "google_calendar_create_events",
                         "--server", "google-calendar", "--input", input_json],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        log.info(f"  Calendar OK: {titulo} (vence {fecha_vencimiento.date()})")
                        stats["eventos_calendar_creados"] += 1
                        stats["plazos_calendar"].append({
                            "titulo": titulo,
                            "fecha": str(fecha_vencimiento.date()),
                            "tipo": "plazo",
                            "dias": n_dias,
                        })
                    else:
                        log.warning(f"  Calendar ERROR plazo: {result.stderr[:100]}")
                except Exception as e:
                    log.warning(f"  Calendar plazo excepción: {e}")


# ─────────────────────────────────────────────
# FASE 6: INFORME Y CORREO HTML
# ─────────────────────────────────────────────
def generar_informe(pdfs_clasificados: list, fecha_str: str) -> Path:
    """Genera informe resumen en Markdown."""
    log.info("=" * 60)
    log.info("FASE 6: Generando informe")
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
        f"| Duplicados omitidos | {stats['pdfs_duplicados_omitidos']} |",
        f"| PDFs clasificados | {stats['pdfs_clasificados']} |",
        f"| Guardados en Notificaciones | {stats['pdfs_guardados_notificaciones']} |",
        f"| Copiados a Usu2 | {stats['pdfs_copiados_usu2']} |",
        f"| Sin mapeo (pendientes) | {stats['pdfs_sin_mapeo']} |",
        f"| Eventos Calendar creados | {stats['eventos_calendar_creados']} |",
        f"| Errores | {len(stats['errores'])} |",
        "",
    ]

    # Documentos mapeados por carpeta
    por_carpeta: dict = {}
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
        lineas.append("| Archivo | Procedimiento | Tipo | Plazos |")
        lineas.append("|:---|:---|:---|:---|")
        for doc in docs:
            plazos_str = "; ".join(doc.get("plazos", []))[:80] if doc.get("plazos") else "-"
            lineas.append(f"| {doc['nombre_descriptivo']} | {doc.get('proc_normalizado','N/A')} | {doc['tipo_resolucion']} | {plazos_str} |")
        lineas.append("")

    # Sin mapeo
    sin_asignar = por_carpeta.get("SIN ASIGNAR", [])
    if sin_asignar:
        lineas.append("## Documentos NO Mapeados (Pendientes de Revisión)")
        lineas.append("")
        lineas.append("| Archivo | Procedimiento | Asunto Correo |")
        lineas.append("|:---|:---|:---|")
        for doc in sin_asignar:
            lineas.append(f"| {doc['nombre_descriptivo']} | {doc.get('proc_normalizado','N/A')} | {doc.get('subject','')[:50]} |")
        lineas.append("")
        lineas.append(f"> Los expedientes sin mapeo se han registrado en `{PENDING_MAPEO_PATH}` para revisión.")
        lineas.append("")

    # Plazos en Calendar
    if stats["plazos_calendar"]:
        lineas.append("## Plazos Registrados en Google Calendar")
        lineas.append("")
        lineas.append("| Evento | Fecha | Tipo |")
        lineas.append("|:---|:---|:---|")
        for ev in stats["plazos_calendar"]:
            lineas.append(f"| {ev['titulo']} | {ev['fecha']} | {ev['tipo']} |")
        lineas.append("")

    # Errores
    if stats["errores"]:
        lineas.append("## Errores")
        lineas.append("")
        for err in stats["errores"]:
            lineas.append(f"- {err}")
        lineas.append("")

    with open(informe_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))

    # JSON de estadísticas
    stats_path = work_dir / "estadisticas.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in stats.items()}, f, ensure_ascii=False, indent=2, default=str)

    log.info(f"Informe: {informe_path}")
    return informe_path


def enviar_correo_resumen(pdfs_clasificados: list, informe_path: Path, fecha_str: str):
    """Envía correo HTML de resumen con tabla de documentos y plazos."""
    log.info("=" * 60)
    log.info("FASE 7: Enviando correo de resumen")
    log.info("=" * 60)

    hoy = datetime.now()
    destinatario = CFG.get("email_resumen", "santiago@aserges.es")

    # Construir tabla HTML de documentos clasificados
    filas_docs = ""
    por_carpeta: dict = {}
    for pdf in pdfs_clasificados:
        carpeta = pdf.get("carpeta_usu2", "SIN ASIGNAR")
        por_carpeta.setdefault(carpeta, []).append(pdf)

    for carpeta, docs in sorted(por_carpeta.items()):
        if carpeta == "SIN ASIGNAR":
            continue
        for doc in docs:
            plazos_str = "<br>".join(doc.get("plazos", []))[:120] if doc.get("plazos") else "-"
            filas_docs += f"""
            <tr>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{carpeta}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{doc.get('proc_normalizado','N/A')}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{doc['tipo_resolucion']}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;font-size:12px;color:#c0392b;">{plazos_str}</td>
            </tr>"""

    # Sin mapeo
    filas_sin_mapeo = ""
    sin_asignar = por_carpeta.get("SIN ASIGNAR", [])
    for doc in sin_asignar:
        filas_sin_mapeo += f"""
            <tr style="background:#fff3cd;">
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{doc['nombre_descriptivo']}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{doc.get('proc_normalizado','N/A')}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{doc.get('subject','')[:50]}</td>
            </tr>"""

    # Plazos en Calendar
    filas_calendar = ""
    for ev in stats.get("plazos_calendar", []):
        filas_calendar += f"""
            <tr>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{ev['titulo']}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{ev['fecha']}</td>
                <td style="padding:4px 8px;border-bottom:1px solid #eee;">{ev['tipo']}</td>
            </tr>"""

    html_body = f"""
<html>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:900px;margin:auto;">
<h2 style="color:#1a5276;border-bottom:2px solid #1a5276;padding-bottom:8px;">
  📋 Pipeline Judicial Diario — {hoy.strftime('%d/%m/%Y %H:%M')}
</h2>
<p>Periodo procesado: <strong>{fecha_str.replace('_', ' ')}</strong></p>

<h3 style="color:#2874a6;">Resumen</h3>
<table style="border-collapse:collapse;width:100%;margin-bottom:20px;">
  <tr style="background:#2874a6;color:white;">
    <th style="padding:8px;text-align:left;">Métrica</th>
    <th style="padding:8px;text-align:right;">Valor</th>
  </tr>
  <tr><td style="padding:6px 8px;">Correos revisados</td><td style="padding:6px 8px;text-align:right;">{stats['correos_revisados']}</td></tr>
  <tr style="background:#f2f3f4;"><td style="padding:6px 8px;">PDFs descargados</td><td style="padding:6px 8px;text-align:right;">{stats['pdfs_descargados']}</td></tr>
  <tr><td style="padding:6px 8px;">Duplicados omitidos</td><td style="padding:6px 8px;text-align:right;">{stats['pdfs_duplicados_omitidos']}</td></tr>
  <tr style="background:#f2f3f4;"><td style="padding:6px 8px;">Copiados a Usu2</td><td style="padding:6px 8px;text-align:right;"><strong>{stats['pdfs_copiados_usu2']}</strong></td></tr>
  <tr><td style="padding:6px 8px;">Sin mapeo (pendientes)</td><td style="padding:6px 8px;text-align:right;color:#c0392b;">{stats['pdfs_sin_mapeo']}</td></tr>
  <tr style="background:#f2f3f4;"><td style="padding:6px 8px;">Eventos Calendar creados</td><td style="padding:6px 8px;text-align:right;">{stats['eventos_calendar_creados']}</td></tr>
</table>

{'<h3 style="color:#2874a6;">Documentos Clasificados en Usu2</h3><table style="border-collapse:collapse;width:100%;margin-bottom:20px;"><tr style="background:#2874a6;color:white;"><th style="padding:8px;text-align:left;">Cliente</th><th style="padding:8px;text-align:left;">Procedimiento</th><th style="padding:8px;text-align:left;">Tipo</th><th style="padding:8px;text-align:left;">Plazos detectados</th></tr>' + filas_docs + '</table>' if filas_docs else ''}

{'<h3 style="color:#c0392b;">⚠️ Documentos Sin Mapeo (Pendientes)</h3><table style="border-collapse:collapse;width:100%;margin-bottom:20px;"><tr style="background:#c0392b;color:white;"><th style="padding:8px;text-align:left;">Archivo</th><th style="padding:8px;text-align:left;">Procedimiento</th><th style="padding:8px;text-align:left;">Asunto</th></tr>' + filas_sin_mapeo + '</table>' if filas_sin_mapeo else ''}

{'<h3 style="color:#27ae60;">📅 Plazos Registrados en Google Calendar</h3><table style="border-collapse:collapse;width:100%;margin-bottom:20px;"><tr style="background:#27ae60;color:white;"><th style="padding:8px;text-align:left;">Evento</th><th style="padding:8px;text-align:left;">Fecha</th><th style="padding:8px;text-align:left;">Tipo</th></tr>' + filas_calendar + '</table>' if filas_calendar else ''}

<p style="font-size:12px;color:#888;border-top:1px solid #eee;padding-top:10px;">
  Generado automáticamente por Pipeline Judicial ASERGES v2.0
</p>
</body>
</html>"""

    # Convertir informe Markdown a PDF para adjuntarlo
    informe_pdf = None
    try:
        informe_pdf_path = informe_path.with_suffix(".pdf")
        result_pdf = subprocess.run(
            ["manus-md-to-pdf", str(informe_path), str(informe_pdf_path)],
            capture_output=True, text=True, timeout=60
        )
        if result_pdf.returncode == 0 and informe_pdf_path.exists():
            informe_pdf = str(informe_pdf_path)
            log.info(f"  Informe PDF generado: {informe_pdf_path.name}")
    except Exception as e:
        log.warning(f"  No se pudo generar PDF del informe: {e}")

    # Construir cuerpo en texto plano (gmail_send_messages usa 'content' en texto plano)
    lineas_texto = [
        f"Pipeline Judicial ASERGES v2.0 — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Periodo: {fecha_str.replace('_', ' ')}",
        "",
        "RESUMEN:",
        f"  Correos revisados:   {stats['correos_revisados']}",
        f"  PDFs descargados:    {stats['pdfs_descargados']}",
        f"  Duplicados omitidos: {stats['pdfs_duplicados_omitidos']}",
        f"  Copiados a Usu2:     {stats['pdfs_copiados_usu2']}",
        f"  Sin mapeo:           {stats['pdfs_sin_mapeo']}",
        f"  Eventos Calendar:    {stats['eventos_calendar_creados']}",
        "",
    ]

    por_carpeta_email: dict = {}
    for pdf in pdfs_clasificados:
        carpeta = pdf.get("carpeta_usu2", "SIN ASIGNAR")
        por_carpeta_email.setdefault(carpeta, []).append(pdf)

    for carpeta, docs in sorted(por_carpeta_email.items()):
        if carpeta == "SIN ASIGNAR":
            continue
        lineas_texto.append(f"[{carpeta}]")
        for doc in docs:
            plazos_str = " | ".join(doc.get("plazos", []))[:100] if doc.get("plazos") else ""
            aviso_plazo = f"  *** PLAZO: {plazos_str}" if plazos_str else ""
            lineas_texto.append(f"  - {doc['nombre_descriptivo']}{aviso_plazo}")
        lineas_texto.append("")

    sin_asignar_email = por_carpeta_email.get("SIN ASIGNAR", [])
    if sin_asignar_email:
        lineas_texto.append("PENDIENTES SIN MAPEO:")
        for doc in sin_asignar_email:
            lineas_texto.append(f"  - {doc['nombre_descriptivo']} (Proc: {doc.get('proc_normalizado','N/A')})")
        lineas_texto.append("")

    if stats.get("plazos_calendar"):
        lineas_texto.append("PLAZOS REGISTRADOS EN GOOGLE CALENDAR:")
        for ev in stats["plazos_calendar"]:
            lineas_texto.append(f"  - {ev['titulo']} — {ev['fecha']}")
        lineas_texto.append("")

    cuerpo_texto = "\n".join(lineas_texto)

    try:
        msg_data = {
            "subject": f"[Judicial] {fecha_str.replace('_al_', ' al ')} — {stats['pdfs_copiados_usu2']} docs | {stats['pdfs_sin_mapeo']} pendientes",
            "to": [destinatario],
            "content": cuerpo_texto,
        }
        if informe_pdf:
            msg_data["attachments"] = [informe_pdf]

        input_json = json.dumps({"messages": [msg_data]}, ensure_ascii=False)

        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", "gmail_send_messages",
             "--server", "gmail", "--input", input_json],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log.info(f"  Correo preparado para {destinatario} (pendiente confirmación en UI)")
        else:
            log.warning(f"  Error enviando correo: {result.stderr[:200]}")
    except Exception as e:
        log.warning(f"  Excepción enviando correo: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Pipeline Judicial Diario - ASERGES v2.0")
    parser.add_argument("--desde", type=str, help="Fecha inicio (YYYY-MM-DD). Por defecto: hoy.")
    parser.add_argument("--hasta", type=str, help="Fecha fin (YYYY-MM-DD). Por defecto: hoy.")
    parser.add_argument("--no-calendar", action="store_true", help="No crear eventos en Google Calendar.")
    parser.add_argument("--no-email", action="store_true", help="No enviar correo de resumen.")
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
    print("  PIPELINE JUDICIAL DIARIO - ASERGES  v2.0")
    print(f"  Periodo: {fecha_str}")
    print("=" * 60)

    # Fase 1: Descargar
    pdfs = descargar_pdfs_gmail(fecha_inicio, fecha_fin)

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

    # Fase 5: Google Calendar
    if clasificados and not args.no_calendar:
        crear_eventos_calendar(clasificados)

    # Fase 6: Informe
    informe = generar_informe(clasificados, fecha_str)

    # Fase 7: Correo de resumen
    if clasificados and not args.no_email:
        enviar_correo_resumen(clasificados, informe, fecha_str)

    # Resumen en consola
    print("\n" + "=" * 60)
    print("  RESUMEN")
    print("=" * 60)
    print(f"  Correos revisados:        {stats['correos_revisados']}")
    print(f"  PDFs descargados:         {stats['pdfs_descargados']}")
    print(f"  Duplicados omitidos:      {stats['pdfs_duplicados_omitidos']}")
    print(f"  Guardados Notificaciones: {stats['pdfs_guardados_notificaciones']}")
    print(f"  Copiados a Usu2:          {stats['pdfs_copiados_usu2']}")
    print(f"  Sin mapeo:                {stats['pdfs_sin_mapeo']}")
    print(f"  Eventos Calendar:         {stats['eventos_calendar_creados']}")
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
