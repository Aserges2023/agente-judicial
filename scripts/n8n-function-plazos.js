/**
 * Código para usar en nodo "Function" de n8n
 * Recalcula plazos judiciales en días hábiles
 *
 * Copiar todo este contenido en un nodo Function de n8n.
 * Input: items con campos plazo_dias y fecha_resolucion
 * Output: items con campo fecha_limite_actuacion recalculado
 */

// --- Festivos nacionales fijos [mes (0-based), día] ---
const FESTIVOS_FIJOS = [
  [0, 1], [0, 6], [4, 1], [7, 15],
  [9, 12], [10, 1], [11, 6], [11, 8], [11, 25],
];

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

function semanaSanta(year) {
  const pascua = calcularPascua(year);
  const jueves = new Date(pascua);
  jueves.setDate(pascua.getDate() - 3);
  const viernes = new Date(pascua);
  viernes.setDate(pascua.getDate() - 2);
  return [jueves, viernes];
}

function esDiaHabil(date) {
  const dow = date.getDay();
  if (dow === 0 || dow === 6) return false;
  const month = date.getMonth();
  const day = date.getDate();
  if (month === 7) return false; // Agosto inhábil
  for (const [m, d] of FESTIVOS_FIJOS) {
    if (month === m && day === d) return false;
  }
  const [jueves, viernes] = semanaSanta(date.getFullYear());
  if (month === jueves.getMonth() && day === jueves.getDate()) return false;
  if (month === viernes.getMonth() && day === viernes.getDate()) return false;
  return true;
}

function calcularFechaLimite(fechaInicio, diasHabiles) {
  const fecha = new Date(fechaInicio);
  let diasContados = 0;
  while (diasContados < diasHabiles) {
    fecha.setDate(fecha.getDate() + 1);
    if (esDiaHabil(fecha)) diasContados++;
  }
  return fecha;
}

// --- Procesar cada item ---
const results = [];
for (const item of $input.all()) {
  const data = item.json;
  const plazoDias = parseInt(data.plazo_dias) || 0;

  if (plazoDias > 0 && data.fecha_resolucion) {
    const fechaBase = new Date(data.fecha_resolucion);
    const fechaLimite = calcularFechaLimite(fechaBase, plazoDias);
    const fechaLimiteStr = fechaLimite.toISOString().split('T')[0];

    results.push({
      json: {
        ...data,
        fecha_limite_actuacion: fechaLimiteStr,
        plazo_dias_habiles: plazoDias,
        _plazo_nota: `${plazoDias} días hábiles judiciales desde ${data.fecha_resolucion}`,
      }
    });
  } else {
    results.push({
      json: {
        ...data,
        fecha_limite_actuacion: null,
        plazo_dias_habiles: 0,
        _plazo_nota: 'Sin plazo o sin fecha de resolución',
      }
    });
  }
}

return results;
