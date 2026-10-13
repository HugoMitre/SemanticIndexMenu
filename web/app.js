/* ══════════════════════════════════════════════════════════════════════
   ICN · lógica de la interfaz

   Sin dependencias externas: los gráficos se construyen como SVG a mano,
   para que la aplicación funcione sin conexión una vez descargado el modelo.
   ═════════════════════════════════════════════════════════════════════ */

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const COMPONENTES = [
  { k: 'D', nombre: 'Desalineación', color: 'var(--comp-d)' },
  { k: 'V', nombre: 'Dispersión',    color: 'var(--comp-v)' },
  { k: 'X', nombre: 'Migración',     color: 'var(--comp-x)' },
  { k: 'A', nombre: 'Amplitud',      color: 'var(--comp-a)' },
  { k: 'P', nombre: 'Profundidad',   color: 'var(--comp-p)' },
];

let sondeo = null;
let ultimoResultado = null;

/* ───────────────────────── utilidades ───────────────────────── */

function avisar(texto, esError = false) {
  const a = $('#aviso');
  a.textContent = texto;
  a.classList.toggle('error', esError);
  a.hidden = false;
  clearTimeout(a._t);
  a._t = setTimeout(() => { a.hidden = true; }, 4200);
}

async function api(ruta, opciones = {}) {
  const r = await fetch(ruta, {
    headers: { 'Content-Type': 'application/json' },
    ...opciones,
  });
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(cuerpo.detail || `Error ${r.status}`);
  return cuerpo;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const n3 = (x) => (x === null || x === undefined || Number.isNaN(x)) ? '—' : Number(x).toFixed(3);
const n2 = (x) => (x === null || x === undefined || Number.isNaN(x)) ? '—' : Number(x).toFixed(2);

function svg(alto, contenido, ancho = 1000) {
  return `<svg viewBox="0 0 ${ancho} ${alto}" width="100%" height="${alto}"
    preserveAspectRatio="xMidYMid meet" role="img">${contenido}</svg>`;
}

/* ───────────────────────── portales ───────────────────────── */

function pintarPortales(portales) {
  const ul = $('#lista-portales');
  $('#conteo').textContent = portales.length;
  $('#lista-vacia').hidden = portales.length > 0;
  $('#btn-vaciar').hidden = portales.length === 0;
  $('#btn-analizar').disabled = portales.length === 0;

  ul.innerHTML = portales.map((p) => `
    <li>
      <span class="nombre">${esc(p.nombre)}</span>
      <span class="url">${esc(p.url)}</span>
      <button class="quitar" data-id="${esc(p.id)}"
              title="Quitar ${esc(p.nombre)}" aria-label="Quitar ${esc(p.nombre)}">×</button>
    </li>`).join('');

  $$('#lista-portales .quitar').forEach((b) => {
    b.onclick = async () => {
      const r = await api(`/api/portales/${b.dataset.id}`, { method: 'DELETE' });
      pintarPortales(r.portales);
    };
  });
}

async function cargarPortales() {
  pintarPortales((await api('/api/portales')).portales);
}

$('#forma-uno').onsubmit = async (e) => {
  e.preventDefault();
  const url = $('#campo-url').value.trim();
  if (!url) return;
  try {
    const r = await api('/api/portales', {
      method: 'POST',
      body: JSON.stringify({ url, nombre: $('#campo-nombre').value.trim() }),
    });
    pintarPortales(r.portales);
    $('#campo-url').value = '';
    $('#campo-nombre').value = '';
    $('#campo-nombre').focus();
  } catch (err) { avisar(err.message, true); }
};

$('#btn-lote').onclick = async () => {
  const texto = $('#campo-lote').value;
  if (!texto.trim()) return;
  try {
    const r = await api('/api/portales/lote', {
      method: 'POST', body: JSON.stringify({ texto }),
    });
    pintarPortales(r.portales);
    $('#campo-lote').value = '';
    let m = `${r.nuevos} portales agregados`;
    if (r.rechazados.length) m += `, ${r.rechazados.length} líneas no se entendieron`;
    avisar(m);
  } catch (err) { avisar(err.message, true); }
};

$('#campo-archivo').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('archivo', f);
  const r = await fetch('/api/portales/archivo', { method: 'POST', body: fd });
  const cuerpo = await r.json();
  pintarPortales(cuerpo.portales);
  avisar(`${cuerpo.nuevos} portales agregados desde ${f.name}`);
  e.target.value = '';
};

$('#btn-catalogo').onclick = async () => {
  const r = await api('/api/portales/catalogo', { method: 'POST' });
  pintarPortales(r.portales);
  avisar(`${r.nuevos} entidades agregadas`);
};

$('#btn-vaciar').onclick = async () => {
  if (!confirm('¿Quitar todos los portales de la lista?')) return;
  pintarPortales((await api('/api/portales', { method: 'DELETE' })).portales);
};

/* ───────────────────────── análisis ───────────────────────── */

$('#btn-analizar').onclick = async () => {
  const op = {
    render:      $('#op-render').checked,
    interactivo: $('#op-interactivo').checked,
    demo:        $('#op-demo').checked,
    ignorar_robots: $('#op-robots').checked,
    percentil:   parseFloat($('#op-percentil').value) || 95,
  };
  try {
    await api('/api/analizar', { method: 'POST', body: JSON.stringify(op) });
    $('#btn-analizar').disabled = true;
    $('#btn-cancelar').hidden = false;
    $('#progreso').hidden = false;
    $('#resultados').hidden = true;
    $('#barra-relleno').classList.remove('error');
    arrancarSondeo();
  } catch (err) { avisar(err.message, true); }
};

$('#btn-cancelar').onclick = async () => {
  await api('/api/cancelar', { method: 'POST' });
  avisar('Cancelando al terminar el portal en curso…');
};

function arrancarSondeo() {
  clearInterval(sondeo);
  sondeo = setInterval(async () => {
    let e;
    try { e = await api('/api/estado'); } catch { return; }

    $('#barra-relleno').style.width = `${e.pct}%`;
    $('#progreso-pct').textContent = `${e.pct.toFixed(0)}%`;
    $('#progreso-mensaje').textContent = e.mensaje || '…';

    $('#bitacora').innerHTML = e.bitacora.slice().reverse().map((b) =>
      `<li><span class="t">${b.t.toFixed(0)}s</span>
           <span class="${esc(b.nivel)}">${esc(b.texto)}</span></li>`).join('');

    if (e.terminado) {
      clearInterval(sondeo);
      $('#btn-analizar').disabled = false;
      $('#btn-cancelar').hidden = true;
      if (e.error) {
        $('#barra-relleno').classList.add('error');
        avisar(e.error, true);
      } else {
        avisar(`Análisis terminado en ${e.segundos.toFixed(0)} s`);
        cargarResultados();
      }
    }
  }, 500);
}

/* ───────────────────────── resultados ───────────────────────── */

async function cargarResultados() {
  let r;
  try { r = await api('/api/resultados'); } catch { return; }
  ultimoResultado = r;
  $('#resultados').hidden = false;

  const st = r.estadisticos;
  $('#resumen-meta').textContent =
    `${r.portales.length} portales · modelo ${r.modelo} · ` +
    `n_ref = ${r.n_ref.n_ref} (P${r.n_ref.percentil}) · ` +
    `${r.segundos} s` + (r.omitidos ? ` · ${r.omitidos} omitidos` : '');

  dibujarTira(r, st);
  dibujarBarras(r);
  tablaPortales(r);
  histogramaPi(st);
  tablaDescriptivos(st);
  matrizCorrelacion(st);
  bloqueSensibilidad(st);
  bloqueParametros(r);
  selectorPeores(r);

  $('#resultados').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* Gráfico de tiras: dónde cae cada portal sobre el eje del índice */
function dibujarTira(r, st) {
  const W = 1000, m = { l: 20, r: 20, t: 34, b: 34 };
  // Alto en función de cuántos niveles hagan falta (estimado por agrupamiento)
  const H = Math.max(150, m.t + m.b + 34 + 22 * Math.min(r.portales.length, 8));
  const x = (v) => m.l + v * (W - m.l - m.r);
  const q = st.suficiente ? st.cuartiles : null;
  let s = '';

  if (q) {
    s += `<rect x="${x(q.q1)}" y="${m.t}" width="${x(q.q3) - x(q.q1)}"
            height="${H - m.t - m.b}" fill="var(--pertenece-claro)" opacity="0.5"/>`;
    s += `<line x1="${x(q.mediana)}" y1="${m.t}" x2="${x(q.mediana)}" y2="${H - m.b}"
            stroke="var(--tinta)" stroke-width="1.5"/>`;
    s += `<text x="${x(q.mediana)}" y="${m.t - 16}" font-size="12" text-anchor="middle"
            fill="var(--tinta-dos)">mediana ${n3(q.mediana)}</text>`;
    s += `<text x="${x(q.q1)}" y="${H - m.b + 30}" font-size="11" text-anchor="middle"
            fill="var(--tinta-tres)">Q1</text>`;
    s += `<text x="${x(q.q3)}" y="${H - m.b + 30}" font-size="11" text-anchor="middle"
            fill="var(--tinta-tres)">Q3</text>`;
  }

  s += `<line x1="${m.l}" y1="${H - m.b}" x2="${W - m.r}" y2="${H - m.b}"
          stroke="var(--linea)" stroke-width="1"/>`;
  for (let v = 0; v <= 1.0001; v += 0.1) {
    s += `<line x1="${x(v)}" y1="${H - m.b}" x2="${x(v)}" y2="${H - m.b + 5}"
            stroke="var(--linea)"/>
          <text x="${x(v)}" y="${H - m.b + 18}" font-size="11" text-anchor="middle"
            fill="var(--tinta-tres)">${v.toFixed(1)}</text>`;
  }

  // Reparto en niveles: cada portal baja al primer nivel donde su etiqueta no
  // choque con la última colocada ahí. Con valores agrupados —que es lo normal
  // en un corpus homogéneo— esto evita que los nombres se enciman.
  const ord = r.portales.slice().sort((a, b) => a.ICN - b.ICN);
  const derecha = [];        // borde derecho de la última etiqueta de cada nivel
  ord.forEach((p) => {
    const px = x(p.ICN);
    // El rótulo va centrado en el punto, así que ocupa px ± ancho/2:
    // hay que comparar BORDES, no centros.
    const ancho = p.clave.length * 6.6 + 6;
    const izq = px - ancho / 2;
    let nivel = derecha.findIndex((d) => izq >= d + 7);
    if (nivel === -1) nivel = derecha.length;
    derecha[nivel] = px + ancho / 2;
    const py = m.t + 12 + nivel * 22;
    const col = p.cuartil >= 4 ? 'var(--desvia)' : 'var(--tinta)';
    s += `<line x1="${px}" y1="${py}" x2="${px}" y2="${H - m.b}"
            stroke="${col}" stroke-width="1" opacity="0.45"/>`;
    s += `<circle cx="${px}" cy="${py}" r="4" fill="${col}"/>`;
    s += `<text x="${px}" y="${py - 8}" font-size="11.5" text-anchor="middle"
            fill="${col}">${esc(p.clave)}</text>`;
    s += `<title>${esc(p.nombre)} · ICN ${n3(p.ICN)} · percentil ${p.percentil_ICN}</title>`;
  });

  $('#tira-icn').innerHTML = svg(H, s);
}

/* Barras apiladas: aportación ponderada de cada componente al ICN */
function dibujarBarras(r) {
  const pesos = r.pesos;
  const filas = r.portales.slice().sort((a, b) => b.ICN - a.ICN);
  const W = 1000, altoFila = 26, m = { l: 150, r: 70, t: 22, b: 24 };
  const H = m.t + filas.length * altoFila + m.b;
  const maxV = Math.max(...filas.map((p) => p.ICN), 0.1);
  const esc_x = (v) => (v / (maxV * 1.12)) * (W - m.l - m.r);
  let s = '';

  for (let v = 0; v <= maxV * 1.12; v += 0.1) {
    const px = m.l + esc_x(v);
    s += `<line x1="${px}" y1="${m.t - 6}" x2="${px}" y2="${H - m.b + 2}"
            stroke="var(--linea-sutil)"/>
          <text x="${px}" y="${H - m.b + 16}" font-size="11" text-anchor="middle"
            fill="var(--tinta-tres)">${v.toFixed(1)}</text>`;
  }

  filas.forEach((p, i) => {
    const y = m.t + i * altoFila;
    let acum = 0;
    s += `<text x="${m.l - 10}" y="${y + 13}" font-size="12.5" text-anchor="end"
            fill="var(--tinta)">${esc(p.nombre.slice(0, 22))}</text>`;
    COMPONENTES.forEach((c) => {
      const w = pesos[c.k] * p[c.k];
      const x0 = m.l + esc_x(acum), ancho = Math.max(esc_x(w), 0);
      s += `<rect x="${x0}" y="${y + 3}" width="${ancho}" height="${altoFila - 9}"
              fill="${c.color}"><title>${esc(p.nombre)} · ${c.nombre}: ${n3(p[c.k])}
              × peso ${pesos[c.k]} = ${n3(w)}</title></rect>`;
      acum += w;
    });
    s += `<text x="${m.l + esc_x(p.ICN) + 8}" y="${y + 13}" font-size="12.5"
            fill="var(--tinta)" font-weight="600">${n3(p.ICN)}</text>`;
  });

  $('#barras-componentes').innerHTML = svg(H, s);
  $('#leyenda-componentes').innerHTML = COMPONENTES.map((c) =>
    `<span><i style="background:${c.color}"></i>${c.nombre} · peso ${pesos[c.k]}</span>`).join('');
}

function tablaPortales(r) {
  const cab = ['Portal', 'Menús', 'Subm.', 'Prof.', 'D', 'V', 'X', 'A', 'P',
               'ICN', 'Pct', 'Q', 'π', 'IC 95% de π', 'Mal ubic.'];
  let h = `<thead><tr>${cab.map((c, i) =>
    `<th${i ? ' class="num"' : ''}>${c}</th>`).join('')}</tr></thead><tbody>`;
  r.portales.forEach((p) => {
    h += `<tr>
      <td class="destacado">${esc(p.nombre)}${
        p.precision_ok === false
          ? ' <span class="malo" title="Intervalo de π demasiado ancho: calibrado con pocos pares nulos">△</span>'
          : ''}</td>
      <td class="num">${p.n_menus}</td>
      <td class="num">${p.n_submenus}</td>
      <td class="num">${p.profundidad}</td>
      <td class="num">${n3(p.D)}</td><td class="num">${n3(p.V)}</td>
      <td class="num">${n3(p.X)}</td><td class="num">${n3(p.A)}</td>
      <td class="num">${n3(p.P)}</td>
      <td class="num destacado">${n3(p.ICN)}</td>
      <td class="num">${p.percentil_ICN ?? '—'}</td>
      <td class="num">${p.cuartil ?? '—'}</td>
      <td class="num ${p.pi_media < 0.5 ? 'malo' : 'bueno'}">${n3(p.pi_media)}</td>
      <td class="num ${p.precision_ok === false ? 'malo' : ''}">${
        p.pi_ic_bajo === null || p.pi_ic_bajo === undefined ? '—'
        : `${n3(p.pi_ic_bajo)}–${n3(p.pi_ic_alto)}`}</td>
      <td class="num ${p.mal_ubicados > 0 ? 'malo' : ''}">${p.mal_ubicados}
        <span style="color:var(--tinta-tres)">(${(p.prop_mal_ubicados * 100).toFixed(0)}%)</span></td>
    </tr>`;
  });
  $('#tabla-portales').innerHTML = h + '</tbody>';

  const flojos = r.portales.filter((p) => p.precision_ok === false);
  const previo = document.querySelector('#aviso-precision');
  if (previo) previo.remove();
  if (flojos.length) {
    $('#tabla-portales').closest('.bloque').insertAdjacentHTML('beforeend',
      `<div class="nota-bloque alerta" id="aviso-precision">
        <strong>${flojos.length} portales marcados con △ no entran en los
        estadísticos de corpus.</strong> Sus valores siguen en la tabla pero no
        son comparables con los demás: casi siempre significa que la extracción
        recuperó pocos submenús, no que el portal sea simple. Reinténtalos con
        renderizado o interacción.
        <ul style="margin:8px 0 0 18px;padding:0">${flojos.map((p) =>
          `<li><strong>${esc(p.nombre)}</strong> — ${
            (p.precision_motivos || []).map(esc).join('; ') || 'precisión insuficiente'}</li>`
        ).join('')}</ul></div>`);
  }
}

function histogramaPi(st) {
  const d = st.distribucion_pi;
  if (!d) { $('#histograma-pi').innerHTML = ''; return; }
  const W = 1000, H = 210, m = { l: 42, r: 20, t: 16, b: 40 };
  const maxC = Math.max(...d.conteo);
  const bw = (W - m.l - m.r) / d.conteo.length;
  let s = '';

  d.conteo.forEach((c, i) => {
    const centro = (d.bordes[i] + d.bordes[i + 1]) / 2;
    const alto = maxC ? (c / maxC) * (H - m.t - m.b) : 0;
    const col = centro < 0.30 ? 'var(--desvia)'
              : centro < 0.50 ? 'var(--desvia-claro)'
              : centro < 0.70 ? 'var(--azar)' : 'var(--pertenece)';
    s += `<rect x="${m.l + i * bw + 1}" y="${H - m.b - alto}" width="${bw - 2}"
            height="${alto}" fill="${col}" opacity="0.85">
            <title>π ${d.bordes[i].toFixed(2)}–${d.bordes[i + 1].toFixed(2)}: ${c} pares</title>
          </rect>`;
  });

  const xAzar = m.l + 0.5 * (W - m.l - m.r);
  s += `<line x1="${xAzar}" y1="${m.t - 6}" x2="${xAzar}" y2="${H - m.b}"
          stroke="var(--tinta)" stroke-width="1.5" stroke-dasharray="4 3"/>
        <text x="${xAzar}" y="${m.t + 4}" font-size="11.5" text-anchor="middle"
          fill="var(--tinta)">azar</text>`;
  s += `<line x1="${m.l}" y1="${H - m.b}" x2="${W - m.r}" y2="${H - m.b}"
          stroke="var(--linea)"/>`;
  for (let v = 0; v <= 1.0001; v += 0.1) {
    const px = m.l + v * (W - m.l - m.r);
    s += `<text x="${px}" y="${H - m.b + 17}" font-size="11" text-anchor="middle"
            fill="var(--tinta-tres)">${v.toFixed(1)}</text>`;
  }
  s += `<text x="${(W) / 2}" y="${H - 6}" font-size="11.5" text-anchor="middle"
          fill="var(--tinta-dos)">índice de pertenencia π</text>`;
  s += `<text x="${m.l - 8}" y="${m.t + 10}" font-size="11" text-anchor="end"
          fill="var(--tinta-tres)">${maxC}</text>`;

  $('#histograma-pi').innerHTML = svg(H, s);
  $('#pi-resumen').innerHTML = `
    <div class="cifra"><span class="valor">${d.n_pares}</span>
      <span class="rotulo">pares evaluados</span></div>
    <div class="cifra"><span class="valor">${n3(d.media)}</span>
      <span class="rotulo">π medio</span></div>
    <div class="cifra"><span class="valor" style="color:var(--desvia)">${(d.prop_bajo_030 * 100).toFixed(1)}%</span>
      <span class="rotulo">peor que el azar (π&lt;0.30)</span></div>
    <div class="cifra"><span class="valor">${(d.prop_bajo_050 * 100).toFixed(1)}%</span>
      <span class="rotulo">por debajo del azar (π&lt;0.50)</span></div>`;
}

function tablaDescriptivos(st) {
  if (!st.suficiente) { $('#tabla-descriptivos').innerHTML =
    `<tbody><tr><td>${esc(st.nota)}</td></tr></tbody>`; return; }
  const claves = ['D', 'V', 'X', 'A', 'P', 'ICN'];
  let h = `<thead><tr><th>Componente</th><th class="num">Peso</th>
    <th class="num">Media</th><th class="num">DE</th><th class="num">Mín</th>
    <th class="num">Q1</th><th class="num">Mediana</th><th class="num">Q3</th>
    <th class="num">Máx</th><th class="num">RIC</th></tr></thead><tbody>`;
  claves.forEach((k) => {
    const d = st.descriptivos[k];
    const bajo = d.de < 0.02;
    h += `<tr>
      <td class="${k === 'ICN' ? 'destacado' : ''}">${esc(d.nombre)}</td>
      <td class="num">${k === 'ICN' ? '—' : d.peso}</td>
      <td class="num">${n3(d.media)}</td>
      <td class="num ${bajo ? 'malo' : ''}">${n3(d.de)}</td>
      <td class="num">${n3(d.min)}</td><td class="num">${n3(d.q1)}</td>
      <td class="num">${n3(d.mediana)}</td><td class="num">${n3(d.q3)}</td>
      <td class="num">${n3(d.max)}</td><td class="num">${n3(d.rango_iq)}</td>
    </tr>`;
  });
  $('#tabla-descriptivos').innerHTML = h + '</tbody>';

  const previo = document.querySelector('#aviso-constantes');
  if (previo) previo.remove();
  const cc = st.constantes;
  if (cc && cc.hay) {
    const nombres = cc.componentes.map((c) => c.nombre).join(' y ');
    const detalle = cc.componentes.map((c) =>
      `${esc(c.nombre)} vale ${n3(c.valor_constante)} en todos los portales y
       aporta un fijo de ${n3(c.aporte_fijo)}, el ${c.pct_del_icn}% del índice`).join('; ');
    const orden = cc.rho_con_sin === undefined ? '' :
      `<br><br>Si se elimina y se renormalizan los pesos, el orden de los portales
       se conserva casi igual (ρ = ${n3(cc.rho_con_sin)}, τ = ${n3(cc.tau_con_sin)})
       y el rango del índice pasa de ${n3(cc.rango_con[0])}–${n3(cc.rango_con[1])}
       a ${n3(cc.rango_sin[0])}–${n3(cc.rango_sin[1])}.`;
    $('#tabla-descriptivos').insertAdjacentHTML('afterend',
      `<div class="nota-bloque alerta" id="aviso-constantes">
        <strong>${esc(nombres)} no varía en este corpus.</strong> ${detalle}.
        No es inofensivo: consume ${cc.peso_desperdiciado} de peso sin discriminar,
        comprime el rango del índice hacia el centro y hace que las diferencias
        reales parezcan menores. El índice tiene ${cc.dimensiones_efectivas}
        dimensiones efectivas, no ${cc.dimensiones_efectivas + cc.componentes.length}.
        ${orden}<br><br>Antes de eliminarlo, descarta que sea un problema de
        medición y no del índice: si todos los portales salen con la misma
        profundidad porque el extractor no baja más niveles, lo que falla es la
        extracción.</div>`);
  }
}

function matrizCorrelacion(st) {
  if (!st.suficiente) { $('#matriz-correlacion').innerHTML = ''; return; }
  const { etiquetas, matriz, redundantes } = st.correlacion;
  const n = etiquetas.length, celda = 58, m = { l: 92, t: 30 };
  const W = m.l + n * celda + 20, H = m.t + n * celda + 16;
  let s = '';

  etiquetas.forEach((e, j) => {
    s += `<text x="${m.l + j * celda + celda / 2}" y="${m.t - 8}" font-size="12"
            text-anchor="middle" fill="var(--tinta-dos)">${e}</text>`;
  });
  etiquetas.forEach((e, i) => {
    const nombre = st.descriptivos[e] ? st.descriptivos[e].nombre : e;
    s += `<text x="${m.l - 8}" y="${m.t + i * celda + celda / 2 + 4}" font-size="12"
            text-anchor="end" fill="var(--tinta-dos)">${esc(nombre.slice(0, 13))}</text>`;
    matriz[i].forEach((v, j) => {
      const a = (v === null || Number.isNaN(v)) ? 0 : Math.min(Math.abs(v), 1);
      const col = (v === null || Number.isNaN(v)) ? '#f0f2f4'
                : (v >= 0 ? `rgba(31,111,139,${0.10 + a * 0.72})`
                          : `rgba(179,65,47,${0.10 + a * 0.72})`);
      s += `<rect x="${m.l + j * celda}" y="${m.t + i * celda}"
              width="${celda - 2}" height="${celda - 2}" fill="${col}" rx="2"/>
            <text x="${m.l + j * celda + celda / 2 - 1}"
              y="${m.t + i * celda + celda / 2 + 4}" font-size="11.5"
              text-anchor="middle" fill="${a > 0.62 ? '#fff' : 'var(--tinta)'}">${n2(v)}</text>`;
    });
  });

  $('#matriz-correlacion').innerHTML = svg(H, s, W);
  $('#aviso-redundancia').innerHTML = redundantes.length
    ? `<div class="nota-bloque alerta">Redundancia detectada:
        ${redundantes.map((p) => `${p.a}–${p.b} (ρ = ${n2(p.rho)})`).join(', ')}.
        Esos componentes están midiendo prácticamente lo mismo, así que el índice
        los cuenta dos veces. Considera fundirlos o justificar por qué se
        conservan separados.</div>`
    : `<div class="nota-bloque bien">Ningún par de componentes supera ρ = 0.90:
        cada uno aporta información que los demás no capturan, que es lo que se
        espera de un índice formativo.</div>`;
}

function bloqueSensibilidad(st) {
  if (!st.suficiente) { $('#sensibilidad').innerHTML = `<p class="ayuda">${esc(st.nota)}</p>`; return; }
  const s = st.sensibilidad, c = st.consistencia;
  const clase = s.tau_media >= 0.85 ? 'bien' : (s.tau_media >= 0.65 ? '' : 'alerta');
  $('#sensibilidad').innerHTML = `
    <div class="cifras">
      <div class="cifra"><span class="valor">${n3(s.tau_media)}</span>
        <span class="rotulo">τ de Kendall medio</span></div>
      <div class="cifra"><span class="valor">${n3(s.tau_p05)}</span>
        <span class="rotulo">percentil 5 de τ</span></div>
      <div class="cifra"><span class="valor">${(s.prop_tau_mayor_08 * 100).toFixed(0)}%</span>
        <span class="rotulo">muestras con τ ≥ 0.8</span></div>
      <div class="cifra"><span class="valor">${(s.conserva_mas_complejo * 100).toFixed(0)}%</span>
        <span class="rotulo">conserva el portal más complejo</span></div>
      <div class="cifra"><span class="valor">${s.n_muestras}</span>
        <span class="rotulo">remuestreos</span></div>
    </div>
    <div class="nota-bloque ${clase}">${esc(s.lectura)}</div>
    <div class="nota-bloque">α de Cronbach = ${c.alfa ?? '—'}. ${esc(c.nota)}</div>`;
}

function bloqueParametros(r) {
  const nr = r.n_ref;
  const mu = r.portales.map((p) => p.mu0), sg = r.portales.map((p) => p.sigma0);
  const prom = (a) => a.reduce((x, y) => x + y, 0) / (a.length || 1);
  $('#parametros').innerHTML = `
    <div class="cifra"><span class="valor">${nr.n_ref}</span>
      <span class="rotulo">n_ref · P${nr.percentil} del corpus</span></div>
    <div class="cifra"><span class="valor">${(nr.tasa_saturacion * 100).toFixed(1)}%</span>
      <span class="rotulo">menús que saturan la amplitud</span></div>
    <div class="cifra"><span class="valor">${nr.n_observaciones}</span>
      <span class="rotulo">amplitudes observadas</span></div>
    <div class="cifra"><span class="valor">${n3(prom(mu))}</span>
      <span class="rotulo">μ₀ medio · media de la nula</span></div>
    <div class="cifra"><span class="valor">${n3(prom(sg))}</span>
      <span class="rotulo">σ₀ medio · dispersión de la nula</span></div>
    <div class="cifra"><span class="valor">${nr.amplitud_mediana}</span>
      <span class="rotulo">amplitud mediana</span></div>`;
}

function selectorPeores(r) {
  const sel = $('#sel-portal');
  sel.innerHTML = r.portales.map((p) =>
    `<option value="${esc(p.id)}">${esc(p.nombre)} · ICN ${n3(p.ICN)}</option>`).join('');
  sel.onchange = () => tablaPeores(r, sel.value);
  if (r.portales.length) tablaPeores(r, r.portales[0].id);
}

function tablaPeores(r, id) {
  const d = r.detalle[id];
  if (!d) { $('#tabla-peores').innerHTML = ''; return; }
  let h = `<thead><tr><th>Menú padre</th><th>Submenú</th>
    <th class="num">cos</th><th class="num">π</th>
    <th>Mejor padre alternativo</th><th>¿Migra?</th></tr></thead><tbody>`;
  d.peores.forEach((s) => {
    h += `<tr>
      <td>${esc(s.menu)}</td>
      <td class="destacado">${esc(s.submenu)}</td>
      <td class="num">${n3(s.coseno)}</td>
      <td class="num ${s.pertenencia < 0.30 ? 'malo' : (s.pertenencia > 0.7 ? 'bueno' : '')}">${n3(s.pertenencia)}</td>
      <td>${esc(s.mejor_padre)} <span style="color:var(--tinta-tres)">(${n3(s.coseno_mejor_padre)})</span></td>
      <td>${s.migra ? '<span class="malo">sí</span>' : 'no'}</td>
    </tr>`;
  });
  $('#tabla-peores').innerHTML = h + '</tbody>';
}

/* ───────────────────────── arranque ───────────────────────── */

cargarPortales();
api('/api/estado').then((e) => {
  if (e.corriendo) {
    $('#progreso').hidden = false;
    $('#btn-analizar').disabled = true;
    $('#btn-cancelar').hidden = false;
    arrancarSondeo();
  } else {
    // Si ya hay un reporte en disco se muestra aunque el servidor se haya
    // reiniciado: el trabajo terminó, el resultado sigue siendo válido.
    cargarResultados();
  }
}).catch(() => {});
