// The map: a background picture you can switch (satellite, terrain, heat map),
// the hotspot grid on top of it, the selected area, and the fire regime layer.
//
// Uses Leaflet (window.L) and topojson-client (window.topojson) from /vendor.
// The background pictures come from free map services listed in config.js.

import { CONFIG, MONTHS_SHORT } from './config.js?v=ember-12'
import { data } from './data.js'
import { FIRE_RAMP, fireBreaks, fireColor, regimeColor } from './colors.js?v=ember-12'
import { formatNumber, formatPower } from './stats.js'

const MAGENTA = '#B4136B'          // the selected area, on light maps
const MAGENTA_BRIGHT = '#F0529F'   // the same, on dark maps and photos

export function createMap(element, legendElement, { onAreaChange, onRegimeClick }) {
  const L = window.L
  const half = CONFIG.cellSize / 2

  const map = L.map(element, {
    // small zoom steps and a short wait make the mouse wheel feel smooth instead of jumpy
    zoomSnap: 0.1, zoomDelta: 0.5, wheelPxPerZoomLevel: 100, wheelDebounceTime: 25,
    minZoom: 2, maxZoom: 11,
    maxBounds: [[-85, -260], [86, 260]],
    zoomControl: false, attributionControl: false,
    boxZoom: false,   // Shift + drag draws a new area instead
  })
  const zoomControl = L.control.zoom({ position: 'topright' }).addTo(map)

  // A zoom slider that opens between + and - when the mouse is over them (see .zoom-slide in style.css)
  const zoomSlide = document.createElement('div')
  zoomSlide.className = 'zoom-slide'
  zoomSlide.innerHTML = `<input type="range" min="${map.getMinZoom()}" max="${map.getMaxZoom()}" step="0.1" aria-label="Zoom">`
  zoomControl.getContainer().insertBefore(zoomSlide, zoomControl.getContainer().lastChild)
  const zoomInput = zoomSlide.firstChild
  // without these, dragging the slider would also drag the map underneath it
  L.DomEvent.disableClickPropagation(zoomSlide)
  L.DomEvent.disableScrollPropagation(zoomSlide)
  // keep the slider open while it is being dragged, even if the mouse slips off it
  zoomSlide.addEventListener('pointerdown', () => {
    zoomControl.getContainer().classList.add('is-sliding')
    document.addEventListener('pointerup', () => zoomControl.getContainer().classList.remove('is-sliding'), { once: true })
  })
  zoomInput.addEventListener('input', () => map.setZoom(Number(zoomInput.value), { animate: false }))
  map.on('zoom', () => { zoomInput.value = map.getZoom() })
  L.control.attribution({ prefix: false, position: 'bottomright' }).addTo(map)
  // keep the map the right size when the layout changes (a panel opens, the menu hides)
  if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe(element)

  // Layers are stacked in "panes", lowest first:
  // background picture (Leaflet's own tile pane) < hill shading < cells < place names < selected area
  const reliefPane = map.createPane('relief')
  reliefPane.style.zIndex = 250
  reliefPane.style.pointerEvents = 'none'
  reliefPane.style.mixBlendMode = 'multiply'    // the shading darkens the map under it, like a shadow
  const cellPane = map.createPane('cells')
  cellPane.style.zIndex = 300
  const labelPane = map.createPane('labels')
  labelPane.style.zIndex = 350
  labelPane.style.pointerEvents = 'none'
  const areaPane = map.createPane('area')
  areaPane.style.zIndex = 400
  areaPane.style.pointerEvents = 'none'
  const cellCanvas = L.canvas({ pane: 'cells', padding: 0.4 })
  const areaCanvas = L.canvas({ pane: 'area', padding: 0.4 })

  // ---------------------------------------------------------------------------
  // Background pictures
  // ---------------------------------------------------------------------------
  let basemap = 'satellite'
  let baseLayer = null, labelLayer = null, reliefLayer = null

  // Heat map: a NASA MODIS picture in "7-2-1" colours, where burn scars are deep red
  // and plants are green. One day of MODIS is often cloudy, so each tile is built from
  // several days: for every pixel we keep a darker pass, because clouds are bright.
  // Pictures: NASA GIBS (free, no key).
  const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/'
  const SENSORS = ['MODIS_Terra', 'MODIS_Aqua']
  const DAY_OFFSETS = [-4, 0, 4]
  const TODAY = new Date(Date.now() - 864e5).toISOString().slice(0, 10)
  const shiftDate = (date, days) => new Date(Date.parse(date) + days * 864e5).toISOString().slice(0, 10)
  let imageryDate = '2025-08-15'

  const loadImage = url => new Promise(resolve => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = url
  })

  function compositeTile(canvas, coords, date, done) {
    const urls = []
    for (const sensor of SENSORS) for (const offset of DAY_OFFSETS) {
      const day = shiftDate(date, offset)
      if (day > TODAY) continue
      urls.push(`${GIBS}${sensor}_CorrectedReflectance_Bands721/default/${day}/GoogleMapsCompatible_Level9/${coords.z}/${coords.y}/${coords.x}.jpg`)
    }
    Promise.all(urls.map(loadImage)).then(images => {
      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      const passes = []
      for (const img of images) {
        if (!img) continue
        ctx.clearRect(0, 0, 256, 256)
        ctx.drawImage(img, 0, 0, 256, 256)
        try { passes.push(ctx.getImageData(0, 0, 256, 256).data) } catch (err) { /* pixels not readable */ }
      }
      const out = ctx.createImageData(256, 256)
      const o = out.data
      for (let p = 0; p < o.length; p += 4) {
        const seen = []
        for (const pass of passes) {
          const r = pass[p], g = pass[p + 1], b = pass[p + 2]
          if (r + g + b < 24) continue             // black gap between satellite passes
          seen.push([b + 0.5 * g, r, g, b])        // clouds and snow are bright in blue and green
        }
        if (!seen.length) { o[p + 3] = 0; continue }
        seen.sort((x, y) => x[0] - y[0])
        const c = seen[Math.floor((seen.length - 1) * 0.3)]   // a dark pass, but not the darkest (cloud shadow)
        o[p] = c[1]; o[p + 1] = c[2]; o[p + 2] = c[3]; o[p + 3] = 255
      }
      ctx.putImageData(out, 0, 0)
      done(null, canvas)
    })
  }

  const BurnView = L.GridLayer.extend({
    createTile(coords, done) {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 256
      compositeTile(canvas, coords, imageryDate, done)
      return canvas
    },
  })

  function makeBaseLayer() {
    if (basemap === 'nasa') {
      return new BurnView({
        maxNativeZoom: 9, bounds: [[-85, -180], [85, 180]], keepBuffer: 1, className: 'burn-view',
        attribution: 'Imagery: NASA GIBS / Worldview, MODIS Terra and Aqua, bands 7-2-1',
      })
    }
    const source = CONFIG.basemaps[basemap]
    // the class name lets style.css make the terrain map darker and bolder
    return L.tileLayer(source.picture, { maxNativeZoom: 17, attribution: source.credit, className: `tiles-${basemap}` })
  }

  // is the picture behind the cells dark (a photo)? The terrain map is the only light one.
  const onDarkPicture = () => basemap !== 'terrain'

  function drawBasemap() {
    for (const layer of [baseLayer, labelLayer, reliefLayer]) if (layer) map.removeLayer(layer)
    baseLayer = makeBaseLayer().addTo(map)
    // photos have no place names of their own, so borders and names are drawn over them
    const labels = basemap === 'nasa' ? CONFIG.basemaps.satellite.labels : CONFIG.basemaps[basemap].labels
    labelLayer = labels ? L.tileLayer(labels, { pane: 'labels', maxNativeZoom: 17, attribution: 'Places and borders © Esri' }).addTo(map) : null
    // the terrain map gets hill shading on top, so mountains and valleys stand out
    const relief = basemap === 'nasa' ? null : CONFIG.basemaps[basemap].relief
    reliefLayer = relief ? L.tileLayer(relief, { pane: 'relief', maxNativeZoom: 13 }).addTo(map) : null

    // On the terrain map the cells are blended in like ink on paper ("multiply"), so roads and
    // names stay readable through them. On photos they are simply see-through.
    cellPane.style.mixBlendMode = onDarkPicture() ? 'normal' : 'multiply'
    element.classList.toggle('on-dark', onDarkPicture())
    box.setStyle({ color: onDarkPicture() ? MAGENTA_BRIGHT : MAGENTA })
    if (cellValues) setCells(cellValues, cellMetric, cellPeriod)
    if (regimeLayer) regimeLayer.setStyle(regimeStyle)
  }

  function setBasemap(name) {
    basemap = name === 'nasa' || CONFIG.basemaps[name] ? name : 'satellite'
    drawBasemap()
  }

  // the heat map shows the days around this date
  function setImageryDate(date) {
    const d = date > TODAY ? TODAY : date < '2000-03-01' ? '2000-03-01' : date
    if (d === imageryDate) return
    imageryDate = d
    if (basemap === 'nasa' && baseLayer) baseLayer.redraw()
  }
  function imageryPeriod() {
    const last = shiftDate(imageryDate, DAY_OFFSETS[DAY_OFFSETS.length - 1])
    return `${shiftDate(imageryDate, DAY_OFFSETS[0])} to ${last > TODAY ? TODAY : last}`
  }

  // ---------------------------------------------------------------------------
  // City list for the offline place search (not drawn: the map pictures have names)
  // ---------------------------------------------------------------------------
  let places = []
  async function loadPlaces() {
    try {
      const world = await (await fetch(CONFIG.files.basemap)).json()
      places = window.topojson.feature(world, world.objects.places).features.map(f => ({
        name: f.properties.name, lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0],
      }))
    } catch (err) { /* the online search still works without it */ }
    for (const [name, lat, lon] of CONFIG.extraPlaces) places.push({ name, lat, lon })
  }

  // ---------------------------------------------------------------------------
  // Hotspot grid: one square per cell, recoloured whenever the selection changes
  // ---------------------------------------------------------------------------
  const cellGroup = L.featureGroup()
  let cellSquares = []
  let cellValues = null, cellMetric = 'count', cellPeriod = ''

  function buildCells() {
    cellSquares = data.cells.map((c, i) => {
      const square = L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]], {
        renderer: cellCanvas, weight: 1, fillOpacity: 0, opacity: 0,
      })
      square.cellNumber = i
      return square.addTo(cellGroup)
    })
    cellGroup.bindTooltip(layer => {
      const c = data.cells[layer.cellNumber]
      const v = cellValues ? cellValues[cellMetric][layer.cellNumber] : 0
      const where = `${Math.abs(c.lat)}°${c.lat < 0 ? 'S' : 'N'}, ${Math.abs(c.lon)}°${c.lon < 0 ? 'W' : 'E'}`
      if (!v) return `<b>No hotspots</b><br>${cellPeriod}<br><span class="dim">${where}</span>`
      return `<b>${formatNumber(cellValues.count[layer.cellNumber])} hotspots</b><br>` +
        `${formatPower(cellValues.frp[layer.cellNumber])} fire energy<br>${cellPeriod}<br><span class="dim">${where}</span>`
    }, { sticky: true, className: 'map-tip', direction: 'top', offset: [0, -6] })
  }

  // values: { count, frp } with one number per cell. period: text for the tooltip.
  function setCells(values, metric, period) {
    cellValues = values; cellMetric = metric; cellPeriod = period
    const v = values[metric]

    // With a place selected, the colours are ranked against that place and its surroundings,
    // not the whole world. Otherwise Alberta would always look pale next to central Africa.
    let nearby = null
    if (area && !plain) {
      const [s, w, n, e] = area
      const padLat = Math.max(4, n - s), padLon = Math.max(6, e - w)
      nearby = Array.from(v).filter((value, i) => {
        const c = data.cells[i]
        return c.lat >= s - padLat && c.lat <= n + padLat && c.lon >= w - padLon && c.lon <= e + padLon
      })
    }
    const breaks = fireBreaks(nearby && nearby.some(x => x > 0) ? nearby : v)

    // A thin line around every square keeps them apart. The line is always one screen pixel wide,
    // so the grid looks the same zoomed in or out. (Gaps between squares get uneven when zoomed out.)
    const line = onDarkPicture() ? { color: '#000', opacity: 0.25 } : { color: '#fff', opacity: 0.9 }
    cellSquares.forEach((square, i) => {
      const color = fireColor(v[i], breaks)
      if (!color) { square.setStyle({ fillOpacity: 0, opacity: 0 }); return }
      // quiet squares are faint and busy squares are solid, so the map shows through where little burns
      let level = 0
      while (level + 1 < breaks.length && v[i] >= breaks[level + 1]) level++
      const strength = breaks.length > 1 ? level / (breaks.length - 1) : 1
      square.setStyle({ fillColor: color, fillOpacity: (onDarkPicture() ? 0.45 : 0.4) + 0.4 * strength, ...line })
    })
    hotspotLegend = { breaks, metric, period }
    drawLegend()
  }

  // ---------------------------------------------------------------------------
  // Fire regime layer (analysis/fire_regimes makes fire_regimes.geojson)
  // ---------------------------------------------------------------------------
  let regimeLayer = null, regimeList = [], showing = 'hotspots', hotspotLegend = null

  function setRegimes(geojson) {
    const seen = new Map()
    for (const f of geojson.features) {
      const p = f.properties
      if (!seen.has(p.regime_id)) seen.set(p.regime_id, { id: p.regime_id, name: p.regime_name, color: p.color })
    }
    regimeList = [...seen.values()].sort((a, b) => a.id - b.id)
    regimeLayer = L.geoJSON(geojson, { renderer: cellCanvas, style: regimeStyle })
    regimeLayer.on('click', e => { if (onRegimeClick && !plain) onRegimeClick(e.layer.feature.properties, e.latlng) })
    regimeLayer.bindTooltip(layer => {
      const p = layer.feature.properties
      let html = `<b>${p.regime_name}</b>`
      if (p.hotspots_per_year !== undefined) html += `<br>About ${formatNumber(p.hotspots_per_year)} hotspots a year`
      if (p.peak_month) html += `<br>Peaks in ${MONTHS_SHORT[p.peak_month - 1]}`
      if (p.shifted) html += `<br>Changed from "${p.shift_from}" to "${p.shift_to}"`
      return html
    }, { sticky: true, className: 'map-tip', direction: 'top', offset: [0, -6] })
  }

  function regimeStyle(f) {
    const p = f.properties
    return {
      fillColor: regimeColor(p.regime_id, p.color), fillOpacity: onDarkPicture() ? 0.55 : 0.75,
      color: onDarkPicture() ? '#fff' : '#17231D', opacity: p.shifted ? 0.9 : 0, weight: p.shifted ? 1.2 : 0,
    }
  }

  function showLayer(name) {
    showing = name === 'regimes' && regimeLayer ? 'regimes' : 'hotspots'
    if (showing === 'regimes') {
      map.removeLayer(cellGroup)
      regimeLayer.addTo(map)
    } else {
      if (regimeLayer) map.removeLayer(regimeLayer)
      cellGroup.addTo(map)
    }
    drawLegend()
  }

  // 5000 -> "5k", 2000000 -> "2M": the key has little room
  function shortNumber(v) {
    if (v >= 1e6) return `${v / 1e6}M`
    if (v >= 1000) return `${v / 1000}k`
    return String(v)
  }

  function drawLegend() {
    if (showing === 'regimes') {
      legendElement.innerHTML = '<p class="legend-title">How each square burns</p>' +
        regimeList.map(r => `<p class="legend-row"><i style="background:${regimeColor(r.id, r.color)}"></i>${r.name}</p>`).join('') +
        '<p class="legend-row"><i class="shifted"></i>Outlined: changed over the years</p>'
      return
    }
    if (!hotspotLegend) return
    const { breaks, metric, period } = hotspotLegend
    const format = metric === 'frp' ? formatPower : shortNumber
    const step = breaks.length > 1 ? (FIRE_RAMP.length - 1) / (breaks.length - 1) : 0
    const swatches = breaks.map((b, i) =>
      `<span><i style="background:${FIRE_RAMP[Math.round(i * step)]}"></i>${format(b)}${i === breaks.length - 1 ? '+' : ''}</span>`).join('')
    legendElement.innerHTML =
      `<p class="legend-title">${metric === 'frp' ? 'Fire energy' : 'Hotspots'} per square</p>` +
      `<p class="legend-period">${period}</p>` +
      `<div class="legend-ramp">${swatches}</div>`
  }

  // ---------------------------------------------------------------------------
  // The selected area: a magenta box with draggable corners
  // ---------------------------------------------------------------------------
  let area = null
  let plain = true      // true until a place is chosen: no box on the map yet
  const WORLD_RING = [[-89, -400], [-89, 400], [89, 400], [89, -400]]
  const shade = L.polygon([WORLD_RING], {
    renderer: areaCanvas, interactive: false, stroke: false, fillColor: '#000', fillOpacity: 0.14,
  })
  const box = L.rectangle([[0, 0], [1, 1]], {
    renderer: areaCanvas, interactive: false, fill: false, color: MAGENTA, weight: 2,
  })
  const handleIcon = L.divIcon({ className: 'area-handle', iconSize: [10, 10] })
  const handles = [0, 1, 2, 3].map(i => {
    const handle = L.marker([0, 0], { icon: handleIcon, draggable: true, bubblingMouseEvents: false, title: 'Drag to resize the area', zIndexOffset: 500 })
    handle.on('drag', () => {
      const p = handle.getLatLng()
      const opposite = handles[(i + 2) % 4].getLatLng()
      drawArea(tidyArea([p.lat, p.lng, opposite.lat, opposite.lng]), handle)
    })
    handle.on('dragend', () => onAreaChange(area))
    return handle
  })
  const areaLayers = [shade, box, ...handles]

  // sorts the corners and rounds to 0.1 degrees
  function tidyArea([latA, lonA, latB, lonB]) {
    const round = v => Math.round(v * 10) / 10
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
    return [
      round(clamp(Math.min(latA, latB), -89, 89)), round(clamp(Math.min(lonA, lonB), -180, 180)),
      round(clamp(Math.max(latA, latB), -89, 89)), round(clamp(Math.max(lonA, lonB), -180, 180)),
    ]
  }

  function drawArea(next, draggedHandle) {
    area = next
    const [s, w, n, e] = area
    box.setBounds([[s, w], [n, e]])
    shade.setLatLngs([WORLD_RING, [[s, w], [s, e], [n, e], [n, w]]])
    const corners = [[s, w], [s, e], [n, e], [n, w]]
    // while a corner is being dragged, leave that one under the pointer
    if (!draggedHandle) handles.forEach((h, i) => h.setLatLng(corners[i]))
  }

  // plain = no place chosen yet, so the box and the shading stay off the map
  function setPlain(next) {
    plain = next
    element.classList.toggle('is-plain', plain)
    for (const layer of areaLayers) plain ? map.removeLayer(layer) : layer.addTo(map)
  }

  function setArea(next, { fit = false } = {}) {
    drawArea(tidyArea([next[0], next[1], next[2], next[3]]))
    if (fit) fitArea()
  }
  function fitArea() {
    // nothing to fit while the map is hidden (its size is 0); it is fitted again when it shows
    if (!area || !element.clientWidth) return
    const [s, w, n, e] = area
    // leave room around the box so you can see where it sits
    // no animation: the map is often still hidden or changing size when this runs, and an
    // animated move that starts then ends up in the wrong place
    map.fitBounds(L.latLngBounds([[s, w], [n, e]]).pad(0.35), { maxZoom: 8, animate: false })
  }

  // Draw a new box by dragging on the map. onFinish() is called when it ends.
  let drawing = false, drawStart = null
  let swallowClick = false      // the click that ends a drag should not also move the box
  function startDrawing(onFinish, firstEvent) {
    drawing = true
    drawStart = firstEvent ? map.mouseEventToLatLng(firstEvent) : null
    const before = area, wasPlain = plain
    map.dragging.disable()
    element.classList.add('is-drawing')
    box.addTo(map)

    const finish = () => {
      drawing = false; drawStart = null
      map.dragging.enable()
      element.classList.remove('is-drawing')
      map.off('mousedown', down); map.off('mousemove', move); map.off('mouseup', up)
      document.removeEventListener('keydown', onKey)
      if (onFinish) onFinish()
    }
    const undo = () => {
      if (before) drawArea(before)
      if (wasPlain) map.removeLayer(box)
    }
    const down = e => { drawStart = e.latlng }
    const move = e => {
      if (drawStart) drawArea(tidyArea([drawStart.lat, drawStart.lng, e.latlng.lat, e.latlng.lng]))
    }
    const up = e => {
      if (!drawStart) return
      const next = tidyArea([drawStart.lat, drawStart.lng, e.latlng.lat, e.latlng.lng])
      const bigEnough = next[2] - next[0] >= 0.2 && next[3] - next[1] >= 0.2
      swallowClick = true
      finish()
      if (bigEnough) { drawArea(next); onAreaChange(next) } else undo()
    }
    const onKey = e => { if (e.key === 'Escape') { finish(); undo() } }
    map.on('mousedown', down); map.on('mousemove', move); map.on('mouseup', up)
    document.addEventListener('keydown', onKey)
  }

  const inside = ll => !plain && area && ll.lat >= area[0] && ll.lat <= area[2] && ll.lng >= area[1] && ll.lng <= area[3]

  // Drag the box from its middle to move it
  let moving = null
  element.addEventListener('mousedown', ev => {
    if (ev.shiftKey || drawing || ev.button !== 0 || ev.target.closest('.leaflet-marker-icon, .leaflet-control, button')) return
    const ll = map.mouseEventToLatLng(ev)
    if (!inside(ll)) return
    ev.stopPropagation()   // keep Leaflet from panning the map
    moving = { start: ll, from: area, moved: false }
    element.classList.add('is-moving')
  }, true)
  document.addEventListener('mousemove', ev => {
    if (!moving) return
    const ll = map.mouseEventToLatLng(ev)
    const dLat = ll.lat - moving.start.lat, dLon = ll.lng - moving.start.lng
    if (Math.abs(dLat) + Math.abs(dLon) > 0.05) moving.moved = true
    const [s, w, n, e] = moving.from
    const shiftLat = Math.min(89 - n, Math.max(-89 - s, dLat)), shiftLon = Math.min(180 - e, Math.max(-180 - w, dLon))
    drawArea(tidyArea([s + shiftLat, w + shiftLon, n + shiftLat, e + shiftLon]))
  })
  document.addEventListener('mouseup', () => {
    if (!moving) return
    element.classList.remove('is-moving')
    if (moving.moved) { swallowClick = true; onAreaChange(area) }
    moving = null
  })
  element.addEventListener('mousemove', ev => {
    if (moving || drawing) return
    element.classList.toggle('over-box', !!inside(map.mouseEventToLatLng(ev)))
  })

  // Click outside the box: move the box there, keeping its size.
  // With no box yet, the first click makes one.
  map.on('click', e => {
    if (swallowClick) { swallowClick = false; return }
    if (drawing || inside(e.latlng)) return
    if (showing === 'regimes' && !plain) return     // on the regime layer a click opens that square's story
    const halfLat = plain || !area ? 3 : (area[2] - area[0]) / 2
    const halfLon = plain || !area ? 5 : (area[3] - area[1]) / 2
    const lat = Math.min(89 - halfLat, Math.max(-89 + halfLat, e.latlng.lat))
    const lon = Math.min(180 - halfLon, Math.max(-180 + halfLon, e.latlng.lng))
    const next = tidyArea([lat - halfLat, lon - halfLon, lat + halfLat, lon + halfLon])
    drawArea(next)
    onAreaChange(next)
  })

  // Shift + drag draws a new box straight away.
  // Runs before Leaflet's own handler so the map doesn't pan.
  element.addEventListener('mousedown', ev => {
    if (!ev.shiftKey || drawing || ev.button !== 0) return
    ev.stopPropagation()
    startDrawing(null, ev)
  }, true)

  // ---------------------------------------------------------------------------
  map.setView([25, 0], 2.25)
  zoomInput.value = map.getZoom()
  drawBasemap()

  return {
    loadPlaces, getPlaces: () => places,
    buildCells, setCells, setRegimes, showLayer,
    redrawColors() { if (cellValues) setCells(cellValues, cellMetric, cellPeriod); if (regimeLayer) regimeLayer.setStyle(regimeStyle); drawLegend() },
    setBasemap, setImageryDate, imageryPeriod,
    setPlain, setArea, fitArea, startDrawing,
    // show or hide the coloured squares (hotspots or patterns), to see the bare map
    setGridVisible(visible) { cellPane.style.display = visible ? '' : 'none' },
    hasRegimes: () => !!regimeLayer,
    refresh: () => map.invalidateSize(),
    showWorld: () => map.setView([25, 0], 2.25),
    openPopup: (html, latlng) => L.popup({ className: 'story-popup', maxWidth: 320, autoPanPadding: [20, 20] }).setLatLng(latlng).setContent(html).openOn(map),
  }
}
