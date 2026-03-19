/**
 * Tests para el calculador de plazos judiciales
 */
const {
  calcularPascua,
  semanaSanta,
  esDiaHabil,
  calcularFechaLimite,
  formatearFecha,
} = require('./plazos-judiciales');

let passed = 0;
let failed = 0;

function assert(condition, message) {
  if (condition) {
    passed++;
    console.log(`  ✓ ${message}`);
  } else {
    failed++;
    console.error(`  ✗ ${message}`);
  }
}

console.log('Test: Cálculo de Pascua');
// Pascua 2026 es 5 de abril
const pascua2026 = calcularPascua(2026);
assert(pascua2026.getMonth() === 3 && pascua2026.getDate() === 5,
  'Pascua 2026 = 5 abril');

// Pascua 2025 es 20 de abril
const pascua2025 = calcularPascua(2025);
assert(pascua2025.getMonth() === 3 && pascua2025.getDate() === 20,
  'Pascua 2025 = 20 abril');

console.log('\nTest: Semana Santa');
const [jueves2026, viernes2026] = semanaSanta(2026);
assert(jueves2026.getMonth() === 3 && jueves2026.getDate() === 2,
  'Jueves Santo 2026 = 2 abril');
assert(viernes2026.getMonth() === 3 && viernes2026.getDate() === 3,
  'Viernes Santo 2026 = 3 abril');

console.log('\nTest: Días hábiles');
// Lunes normal (no festivo, no agosto)
assert(esDiaHabil(new Date(2026, 2, 16)) === true,
  'Lunes 16 marzo 2026 = hábil');
// Sábado
assert(esDiaHabil(new Date(2026, 2, 14)) === false,
  'Sábado 14 marzo 2026 = inhábil');
// Domingo
assert(esDiaHabil(new Date(2026, 2, 15)) === false,
  'Domingo 15 marzo 2026 = inhábil');
// Agosto
assert(esDiaHabil(new Date(2026, 7, 10)) === false,
  'Lunes 10 agosto 2026 = inhábil (agosto)');
// Festivo: 1 enero
assert(esDiaHabil(new Date(2026, 0, 1)) === false,
  '1 enero 2026 = inhábil (festivo)');
// Festivo: 25 diciembre
assert(esDiaHabil(new Date(2026, 11, 25)) === false,
  '25 diciembre 2026 = inhábil (festivo)');
// Jueves Santo
assert(esDiaHabil(new Date(2026, 3, 2)) === false,
  'Jueves Santo 2 abril 2026 = inhábil');
// Viernes Santo
assert(esDiaHabil(new Date(2026, 3, 3)) === false,
  'Viernes Santo 3 abril 2026 = inhábil');

console.log('\nTest: Cálculo de plazos');
// Desde lunes 16 marzo 2026, 10 días hábiles
const limite10 = calcularFechaLimite(new Date(2026, 2, 16), 10);
assert(formatearFecha(limite10) === '2026-03-30',
  '10 días hábiles desde 16/3/2026 = 30/3/2026');

// Desde lunes 16 marzo 2026, 20 días hábiles (cruza Semana Santa)
const limite20 = calcularFechaLimite(new Date(2026, 2, 16), 20);
assert(formatearFecha(limite20) === '2026-04-15',
  '20 días hábiles desde 16/3/2026 = 15/4/2026 (cruza Semana Santa)');

// Plazo que cruza agosto
const limiteAgosto = calcularFechaLimite(new Date(2026, 6, 20), 15);
// Julio 20 es lunes. 15 dias habiles: julio tiene ~8 días hábiles restantes,
// luego agosto inhábil completo, septiembre empieza 1 (martes)
assert(limiteAgosto.getMonth() === 8, // septiembre
  '15 días hábiles desde 20/7/2026 cruza agosto → septiembre');

console.log('\nTest: Formatear fecha');
assert(formatearFecha(new Date(2026, 2, 19)) === '2026-03-19',
  'Formato YYYY-MM-DD correcto');

console.log(`\n--- Resultados: ${passed} passed, ${failed} failed ---`);
process.exit(failed > 0 ? 1 : 0);
