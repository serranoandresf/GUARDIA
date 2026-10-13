// Pruebas de la lógica pura del panel (plan de protección, informe y contraste). Ejecutar: node pruebas/test_panel.js
const fs = require('fs'), assert = require('assert');
const h = fs.readFileSync(__dirname + '/../web/index.html', 'utf8');
const capa = eval('(' + h.match(/const CAPA=(\{[^}]*\});/)[1] + ')');
const m = {}; new Function('m', h.split('// <riesgo>')[1].split('// </riesgo>')[0] + ';m.QS=QS;m.riesgo=riesgo;m.reduccion=reduccion;m.resumen=resumen;m.lum=lum;m.cr=cr')(m);
const items = [['luz', 'Luz', '', 8], ['cer', 'Cerraduras', '', 9], ['con', 'Contactos', '', 5]];
const qs = m.QS.com.concat(m.QS.hogar), todo = v => Object.fromEntries(qs.map(q => [q[0], v]));
for (const q of qs.concat(m.QS.negocio, m.QS.rural)) { assert.equal(q[2].length, 3); q[3].forEach(c => assert(['Disuadir', 'Detectar', 'Demorar', 'Responder'].includes(c))); }
assert.equal(m.riesgo(todo(2), items, qs, capa, new Set()).nivel, 'Alto');
assert.equal(m.riesgo(todo(0), items, qs, capa, new Set()).nivel, 'Bajo');
assert.equal(m.riesgo({ solo: 1 }, items, qs, capa, new Set()).nivel, null);
const r = m.riesgo(todo(2), items, qs, capa, new Set());
assert.equal(r.plan[0].id, 'cer'); assert(r.plan.every((x, i) => !i || r.plan[i - 1].prio >= x.prio));
assert(r.plan[0].motivo.startsWith('Pesa más porque'));
assert(!m.riesgo(todo(2), items, qs, capa, new Set(['cer'])).plan.some(x => x.id == 'cer'));
assert.equal(m.riesgo(todo(0), items, qs, capa, new Set()).plan[0].motivo, 'Medida base recomendada.');
assert.equal(r.plan.reduce((a, x) => a + m.reduccion(r.plan, x.id), 0) >= 99, true);
assert.equal(m.reduccion([], 'x'), 0);
const ahora = Date.UTC(2026, 9, 12, 12), ev = (c, e, dh) => ({ camara: c, estado: e, hora: (ahora - dh * 36e5) / 1000 });
const a = [...Array(6)].map((_, i) => ev('A', i < 4 ? 'falsa_alarma' : 'nuevo', 1)), res = m.resumen([...a, ev('B', 'nuevo', 500)], ahora);
assert.equal(res.total, 6); assert.equal(res.falsas, 4); assert.equal(res.top[0][0], 'A'); assert(res.cons.some(c => c.includes('"A"')));
assert.equal(m.resumen([], ahora).pico, null);
assert(Math.abs(m.cr(m.lum('#ffffff'), m.lum('#000000')) - 21) < 0.1);
assert(m.cr(m.lum('#ffd60a'), m.lum('#eceef4')) < 3);
console.log('Pruebas del panel: OK');
