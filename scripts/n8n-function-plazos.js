/**
 * Código para usar en nodo "Function" de n8n
 * Recalcula plazos judiciales en días hábiles (nacional + autonómico + local)
 *
 * Copiar todo este contenido en un nodo Function de n8n.
 * Input: items con campos plazo_dias, fecha_resolucion, comunidad_autonoma (opcional), localidad_juzgado (opcional)
 * Output: items con campo fecha_limite_actuacion recalculado
 */

// --- Festivos nacionales fijos [mes (0-based), día] ---
const FESTIVOS_FIJOS = [
  [0, 1], [0, 6], [4, 1], [7, 15],
  [9, 12], [10, 1], [11, 6], [11, 8], [11, 25],
];

// --- Festivos autonómicos fijos por CCAA ---
const FESTIVOS_AUTONOMICOS = {
  'Andalucía':           [[1, 28]],
  'Aragón':              [[3, 23]],
  'Asturias':            [[8, 8]],
  'Baleares':            [[2, 1]],
  'Canarias':            [[4, 30]],
  'Cantabria':           [[6, 28]],
  'Castilla-La Mancha':  [[4, 31]],
  'Castilla y León':     [[3, 23]],
  'Cataluña':            [[8, 11], [11, 26]],
  'Extremadura':         [[8, 8]],
  'Galicia':             [[6, 25]],
  'La Rioja':            [[5, 9]],
  'Madrid':              [[4, 2]],
  'Murcia':              [[5, 9]],
  'Navarra':             [[11, 3]],
  'País Vasco':          [],
  'Valencia':            [[9, 9]],
  'Ceuta':               [[8, 2]],
  'Melilla':             [[8, 17]],
};

// --- Festivos locales (configurables) ---
const FESTIVOS_LOCALES = {
  'Madrid':    [[4, 15], [10, 9]],   // San Isidro, Almudena
  'Barcelona': [[8, 24]],            // La Mercè
  'Valencia':  [[2, 19]],            // San José / Fallas
};

function normalizarCCAA(ccaa) {
  if (!ccaa) return null;
  const s = ccaa.trim();
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
    'Catalunya':            'Cataluña',
  };
  if (aliases[s]) return aliases[s];
  if (FESTIVOS_AUTONOMICOS[s]) return s;
  return null;
}

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

function esDiaHabil(date, opts) {
  opts = opts || {};
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

  const ccaa = normalizarCCAA(opts.ccaa);
  if (ccaa && FESTIVOS_AUTONOMICOS[ccaa]) {
    for (const [m, d] of FESTIVOS_AUTONOMICOS[ccaa]) {
      if (month === m && day === d) return false;
    }
  }
  if (opts.localidad && FESTIVOS_LOCALES[opts.localidad]) {
    for (const [m, d] of FESTIVOS_LOCALES[opts.localidad]) {
      if (month === m && day === d) return false;
    }
  }
  return true;
}

function calcularFechaLimite(fechaInicio, diasHabiles, opts) {
  const fecha = new Date(fechaInicio);
  let diasContados = 0;
  while (diasContados < diasHabiles) {
    fecha.setDate(fecha.getDate() + 1);
    if (esDiaHabil(fecha, opts)) diasContados++;
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
    const opts = {
      ccaa: data.comunidad_autonoma,
      localidad: data.localidad_juzgado,
    };
    const fechaLimite = calcularFechaLimite(fechaBase, plazoDias, opts);
    const fechaLimiteStr = fechaLimite.toISOString().split('T')[0];

    // Marca urgencia si aún no viene del análisis IA
    const urgente = typeof data.urgente === 'boolean'
      ? data.urgente
      : plazoDias <= 5;

    results.push({
      json: {
        ...data,
        fecha_limite_actuacion: fechaLimiteStr,
        plazo_dias_habiles: plazoDias,
        urgente,
        _plazo_nota: `${plazoDias} días hábiles judiciales desde ${data.fecha_resolucion}` +
          (opts.ccaa ? ` (CCAA: ${opts.ccaa})` : '') +
          (opts.localidad ? ` (localidad: ${opts.localidad})` : ''),
      }
    });
  } else {
    results.push({
      json: {
        ...data,
        fecha_limite_actuacion: null,
        plazo_dias_habiles: 0,
        urgente: false,
        _plazo_nota: 'Sin plazo o sin fecha de resolución',
      }
    });
  }
}

return results;
