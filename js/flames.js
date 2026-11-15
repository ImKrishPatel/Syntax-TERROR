// The flames behind the Home page.
//
// It is a grid of small squares on a <canvas>. Each square asks "how hot is it
// here right now?" and gets bigger and redder the hotter it is. The heat comes
// from a smooth random pattern ("noise") that drifts upward, strongest at the
// bottom of the screen, plus extra heat around the mouse.
//
// On the other pages the same canvas stays behind the panels, frozen and
// blurred (the blur is done in style.css).

const CELL = 10          // distance between squares, in pixels
const FRAME_MS = 40      // redraw about 25 times a second

// squares from "barely warm" to "hottest", for the light and the dark page
const COLORS = {
  light: ['#F4B544', '#EB872B', '#D6531F', '#AC281C'],
  dark: ['#7A1A14', '#AC281C', '#D6531F', '#EB872B', '#F4B544'],
}

// --- smooth random numbers ---------------------------------------------------
// hash() turns a grid corner into a fixed random number between 0 and 1.
// noise() blends the four corners around a point, so nearby points get nearby values.
function hash(x, y) {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453
  return n - Math.floor(n)
}
function noise(x, y) {
  const x0 = Math.floor(x), y0 = Math.floor(y)
  const fx = x - x0, fy = y - y0
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy)
  const top = hash(x0, y0) + (hash(x0 + 1, y0) - hash(x0, y0)) * sx
  const bottom = hash(x0, y0 + 1) + (hash(x0 + 1, y0 + 1) - hash(x0, y0 + 1)) * sx
  return top + (bottom - top) * sy
}

export function startFlames(canvas) {
  const ctx = canvas.getContext('2d')
  const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  let width = 0, height = 0
  let active = true            // false = frozen (we are not on the Home page)
  let timer = null
  let mouse = null             // { x, y, heat } heat fades after the mouse stops
  const sparks = []            // little embers that float up

  function resize() {
    const ratio = Math.min(2, window.devicePixelRatio || 1)
    width = window.innerWidth
    height = window.innerHeight
    canvas.width = Math.round(width * ratio)
    canvas.height = Math.round(height * ratio)
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    draw()
  }

  // how hot is the point (x, y) at this time? 0 = cold, 1 = hottest
  function heatAt(x, y, time) {
    const up = (height - y) / height                      // 0 at the bottom, 1 at the top
    // the flames reach a different height at each x, and that height slowly changes
    const reach = 0.2 + 0.3 * noise(x / 260, time * 0.12)
    let heat = 0
    if (up < reach) {
      const body = 1 - up / reach                          // 1 at the bottom, 0 at the tip
      const flicker = noise(x / 55, y / 70 + time * 1.1)   // "+ time" makes the pattern rise
      heat = body * body * (0.35 + 0.9 * flicker)
    }
    if (mouse) {
      const dx = x - mouse.x, dy = y - mouse.y
      const near = Math.exp(-(dx * dx + dy * dy) / 9000)   // 1 at the pointer, fading with distance
      heat += near * mouse.heat * (0.45 + 0.55 * noise(x / 30, y / 30 - time * 2))
    }
    return Math.min(1, heat)
  }

  function draw() {
    const time = performance.now() / 1000
    const colors = document.documentElement.dataset.theme === 'dark' ? COLORS.dark : COLORS.light
    ctx.clearRect(0, 0, width, height)

    // skip the rows that are always cold (top of the screen), unless the mouse is up there
    const firstRow = Math.floor(height * 0.45 / CELL)
    for (let row = 0; row * CELL < height; row++) {
      const y = row * CELL + CELL / 2
      const nearMouse = mouse && mouse.heat > 0.02 && Math.abs(y - mouse.y) < 220
      if (row < firstRow && !nearMouse) continue
      for (let col = 0; col * CELL < width; col++) {
        const x = col * CELL + CELL / 2
        const heat = heatAt(x, y, time)
        if (heat < 0.04) continue
        const grain = 0.55 + 0.45 * hash(col, row)          // fixed speckle, so it looks grainy
        const size = CELL * (0.18 + 0.62 * heat)
        ctx.globalAlpha = Math.min(1, heat * 1.3) * grain
        // squaring the heat keeps most squares in the cooler colours; only the hottest reach the last one
        ctx.fillStyle = colors[Math.min(colors.length - 1, Math.floor(heat * heat * colors.length))]
        ctx.fillRect(x - size / 2, y - size / 2, size, size)
      }
    }

    // embers: single squares that drift up from the flames and fade
    ctx.fillStyle = colors[colors.length - 1]
    for (const s of sparks) {
      ctx.globalAlpha = s.life
      const x = Math.round(s.x / CELL) * CELL + CELL / 2, y = Math.round(s.y / CELL) * CELL + CELL / 2
      ctx.fillRect(x - 2, y - 2, 4, 4)
    }
    ctx.globalAlpha = 1
  }

  function step() {
    if (Math.random() < 0.35 && sparks.length < 40) {
      sparks.push({ x: Math.random() * width, y: height * (0.72 + 0.25 * Math.random()), life: 1, speed: 0.6 + Math.random() * 1.4, drift: (Math.random() - 0.5) * 0.8 })
    }
    for (const s of sparks) { s.y -= s.speed; s.x += s.drift; s.life -= 0.008 }
    for (let i = sparks.length - 1; i >= 0; i--) if (sparks[i].life <= 0) sparks.splice(i, 1)
    if (mouse) mouse.heat *= 0.965
    draw()
  }

  function run() {
    clearInterval(timer)
    timer = null
    if (active && !calm && !document.hidden) timer = setInterval(step, FRAME_MS)
    else draw()
  }

  window.addEventListener('resize', resize)
  document.addEventListener('visibilitychange', run)
  window.addEventListener('pointermove', event => {
    if (!active) return
    mouse = { x: event.clientX, y: event.clientY, heat: 1 }
  })

  resize()
  run()

  return {
    // sharp and moving on Home; frozen everywhere else
    setActive(next) { active = next; run() },
    // call after switching light / dark so the frozen picture uses the new colours
    redraw: draw,
  }
}
