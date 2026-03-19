/**
 * Cálculo de plazos judiciales en días hábiles (España)
 *
 * Reglas:
 * - Excluye sábados y domingos
 * - Excluye agosto completo (inhábil judicial)
 * - Excluye festivos nacionales fijos
 * - Excluye Jueves y Viernes Santo (calculados dinámicamente)
 */

// Festivos nacionales fijos (mes-día, base 0 para mes)
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
 * Comprobar si una fecha es día hábil judicial
 * @param {Date} date
 * @returns {boolean}
 */
function esDiaHabil(date) {
  const dow = date.getDay();
  // Sábado o domingo
  if (dow === 0 || dow === 6) return false;

  const month = date.getMonth();
  const day = date.getDate();

  // Agosto completo es inhábil
  if (month === 7) return false;

  // Festivos fijos
  for (const [m, d] of FESTIVOS_FIJOS) {
    if (month === m && day === d) return false;
  }

  // Semana Santa (Jueves y Viernes Santo)
  const [jueves, viernes] = semanaSanta(date.getFullYear());
  if (month === jueves.getMonth() && day === jueves.getDate()) return false;
  if (month === viernes.getMonth() && day === viernes.getDate()) return false;

  return true;
}

/**
 * Calcular fecha límite sumando días hábiles judiciales
 * @param {Date|string} fechaInicio - Fecha de inicio
 * @param {number} diasHabiles - Número de días hábiles a sumar
 * @returns {Date} Fecha límite
 */
function calcularFechaLimite(fechaInicio, diasHabiles) {
  const fecha = new Date(fechaInicio);
  let diasContados = 0;

  while (diasContados < diasHabiles) {
    fecha.setDate(fecha.getDate() + 1);
    if (esDiaHabil(fecha)) {
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
  };
}

// --- Uso en n8n Function node ---
// Copiar las funciones anteriores y usar así:
//
// const fechaResolucion = new Date($json.fecha_resolucion);
// const plazoDias = $json.plazo_dias;
// if (plazoDias && plazoDias > 0) {
//   const fechaLimite = calcularFechaLimite(fechaResolucion, plazoDias);
//   return { ...$json, fecha_limite_actuacion: formatearFecha(fechaLimite) };
// }
// return $json;
