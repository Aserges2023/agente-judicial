/**
 * Cálculo de plazos judiciales en días hábiles (España)
 *
 * Reglas:
 * - Excluye sábados y domingos
 * - Excluye agosto completo (inhábil judicial por LOPJ art. 183)
 * - Excluye festivos nacionales fijos
 * - Excluye Jueves y Viernes Santo (calculados dinámicamente)
 * - Excluye festivos autonómicos fijos (si se indica CCAA)
 *
 * NOTA: Los festivos locales/municipales NO se calculan automáticamente.
 * Si es crítico, configurarlos manualmente en FESTIVOS_LOCALES.
 */

// Festivos nacionales fijos [mes (0-based), día]
const FESTIVOS_FIJOS = [
  [0, 1],   // 1 enero - Año Nuevo
  [0, 6],   // 6 enero - Epifanía
  [4, 1],   // 1 mayo - Día del Trabajo
  [7, 15],  // 15 agosto - Asunción (además agosto es inhábil completo)
  [9, 12],  // 12 octubre - Fiesta Nacional
  [10, 1],  // 1 noviembre - Todos los Santos
  [11, 6],  // 6 diciembre - Constitución
  [11, 8],  // 8 diciembre - Inmaculada
  [11, 25], // 25 diciembre - Navidad
];

/**
 * Festivos autonómicos fijos por CCAA (mes 0-based, día).
 * Solo los festivos que se repiten cada año en la misma fecha.
 * Las fiestas móviles (San José, Lunes de Pascua, Corpus, etc.) no se incluyen aquí.
 */
const FESTIVOS_AUTONOMICOS = {
  'Andalucía':           [[1, 28]],                    // 28 feb - Día de Andalucía
  'Aragón':              [[3, 23]],                    // 23 abr - San Jorge
  'Asturias':            [[8, 8]],                     // 8 sep - Día de Asturias
  'Baleares':            [[2, 1]],                     // 1 mar - Día de las Islas Baleares
  'Canarias':            [[4, 30]],                    // 30 may - Día de Canarias
  'Cantabria':           [[6, 28]],                    // 28 jul - Día de las Instituciones
  'Castilla-La Mancha':  [[4, 31]],                    // 31 may - Día de Castilla-La Mancha
  'Castilla y León':     [[3, 23]],                    // 23 abr - Día de Castilla y León
  'Cataluña':            [[8, 11], [11, 26]],          // 11 sep - Diada; 26 dic - San Esteban
  'Extremadura':         [[8, 8]],                     // 8 sep - Día de Extremadura
  'Galicia':             [[6, 25]],                    // 25 jul - Día de Galicia (Santiago Apóstol)
  'La Rioja':            [[5, 9]],                     // 9 jun - Día de La Rioja
  'Madrid':              [[4, 2]],                     // 2 may - Día de la Comunidad de Madrid
  'Murcia':              [[5, 9]],                     // 9 jun - Día de la Región de Murcia
  'Navarra':             [[11, 3]],                    // 3 dic - San Francisco Javier
  'País Vasco':          [],                           // No tiene festivo autonómico fijo
  'Valencia':            [[9, 9]],                     // 9 oct - Día de la Comunitat Valenciana
  'Ceuta':               [[8, 2]],                     // 2 sep - Día de Ceuta
  'Melilla':             [[8, 17]],                    // 17 sep - Día de Melilla
};

/**
 * Festivos locales adicionales, configurables manualmente.
 * Formato: { 'NombreMunicipio': [[mes, día], ...] }
 * Ejemplo: 'Madrid': [[4, 15], [10, 9]] (15 mayo San Isidro, 9 nov Almudena)
 */
const FESTIVOS_LOCALES = {
  'Madrid':     [[4, 15], [10, 9]],   // San Isidro, Almudena
  'Barcelona':  [[8, 24]],            // La Mercè (24 sep)
  'Sevilla':    [[4, 30]],            // 30 may (aprox. — en la práctica cambia)
  'Valencia':   [[2, 19]],            // 19 mar - San José / Fallas
};

/**
 * Algoritmo de Computus (Anónimo Gregoriano) para calcular Domingo de Pascua
 * @param {number} year
 * @returns {Date} Domingo de Pascua
 */
function calcularPascua(year) {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31) - 1;
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(year, month, day);
}

/**
 * Obtener Jueves y Viernes Santo para un año
 * @param {number} year
 * @returns {Date[]} [juevesSanto, viernesSanto]
 */
function semanaSanta(year) {
  const pascua = calcularPascua(year);
  const jueves = new Date(pascua);
  jueves.setDate(pascua.getDate() - 3);
  const viernes = new Date(pascua);
  viernes.setDate(pascua.getDate() - 2);
  return [jueves, viernes];
}

/**
 * Normaliza el nombre de CCAA para búsqueda en el mapa.
 * @param {string|null} ccaa
 * @returns {string|null}
 */
function normalizarCCAA(ccaa) {
  if (!ccaa) return null;
  const s = ccaa.trim();
  // Alias comunes
  const aliases = {
    'Islas Baleares':       'Baleares',
    'Illes Balears':        'Baleares',
    'Comunidad de Madrid':  'Madrid',
    'Comunidad Valenciana': 'Valencia',
    'Comunitat Valenciana': 'Valencia',
    'Principado de Asturias': 'Asturias',
    'Región de Murcia':     'Murcia',
    'Comunidad Foral de Navarra': 'Navarra',
    'Euskadi':              'País Vasco',
    'Cataluny':             'Cataluña',
    'Catalunya':            'Cataluña',
  };
  if (aliases[s]) return aliases[s];
  if (FESTIVOS_AUTONOMICOS[s]) return s;
  return null;
}

/**
 * Comprobar si una fecha es día hábil judicial
 * @param {Date} date
 * @param {Object} [opts] - Opciones
 * @param {string} [opts.ccaa] - Comunidad autónoma del juzgado
 * @param {string} [opts.localidad] - Localidad del juzgado
 * @returns {boolean}
 */
function esDiaHabil(date, opts = {}) {
  const dow = date.getDay();
  // Sábado o domingo
  if (dow === 0 || dow === 6) return false;

  const month = date.getMonth();
  const day = date.getDate();

  // Agosto completo es inhábil
  if (month === 7) return false;

  // Festivos nacionales fijos
  for (const [m, d] of FESTIVOS_FIJOS) {
    if (month === m && day === d) return false;
  }

  // Semana Santa (Jueves y Viernes Santo)
  const [jueves, viernes] = semanaSanta(date.getFullYear());
  if (month === jueves.getMonth() && day === jueves.getDate()) return false;
  if (month === viernes.getMonth() && day === viernes.getDate()) return false;

  // Festivos autonómicos
  const ccaa = normalizarCCAA(opts.ccaa);
  if (ccaa && FESTIVOS_AUTONOMICOS[ccaa]) {
    for (const [m, d] of FESTIVOS_AUTONOMICOS[ccaa]) {
      if (month === m && day === d) return false;
    }
  }

  // Festivos locales
  if (opts.localidad && FESTIVOS_LOCALES[opts.localidad]) {
    for (const [m, d] of FESTIVOS_LOCALES[opts.localidad]) {
      if (month === m && day === d) return false;
    }
  }

  return true;
}

/**
 * Calcular fecha límite sumando días hábiles judiciales
 * @param {Date|string} fechaInicio - Fecha de inicio
 * @param {number} diasHabiles - Número de días hábiles a sumar
 * @param {Object} [opts] - Opciones (ccaa, localidad)
 * @returns {Date} Fecha límite
 */
function calcularFechaLimite(fechaInicio, diasHabiles, opts = {}) {
  const fecha = new Date(fechaInicio);
  let diasContados = 0;

  while (diasContados < diasHabiles) {
    fecha.setDate(fecha.getDate() + 1);
    if (esDiaHabil(fecha, opts)) {
      diasContados++;
    }
  }

  return fecha;
}

/**
 * Formatear fecha como YYYY-MM-DD
 * @param {Date} date
 * @returns {string}
 */
function formatearFecha(date) {
  return date.toISOString().split('T')[0];
}

// Exportar para uso en n8n (Function nodes) o Node.js
if (typeof module !== 'undefined') {
  module.exports = {
    calcularPascua,
    semanaSanta,
    esDiaHabil,
    calcularFechaLimite,
    formatearFecha,
    normalizarCCAA,
    FESTIVOS_AUTONOMICOS,
    FESTIVOS_LOCALES,
  };
}

// --- Uso en n8n Function node ---
// const fechaResolucion = new Date($json.fecha_resolucion);
// const plazoDias = $json.plazo_dias;
// const opts = { ccaa: $json.comunidad_autonoma, localidad: $json.localidad_juzgado };
// if (plazoDias && plazoDias > 0) {
//   const fechaLimite = calcularFechaLimite(fechaResolucion, plazoDias, opts);
//   return { ...$json, fecha_limite_actuacion: formatearFecha(fechaLimite) };
// }
// return $json;
