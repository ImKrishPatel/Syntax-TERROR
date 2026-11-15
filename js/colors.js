// Colour ramps. The fire ramp gets steadily darker, and the "compared with
// usual" ramp goes blue (less than usual) to grey (usual) to orange-red (more).

// There are two sets of colours. 'fire' is the normal one. 'safe' is for
// colour-blind viewers: it avoids telling things apart by red against green.
// setPalette() swaps them; everything that draws asks for the colours again.
const PALETTES = {
  fire: {
    ramp: ['#F8DC78', '#F4B544', '#EB872B', '#D6531F', '#AC281C', '#6B1215'],
    score: ['#2C6A99', '#8DB3CC', '#D5DAD2', '#F2BE74', '#E2862D', '#BC471E', '#761915'],
  },
  safe: {
    ramp: ['#FDE725', '#7AD151', '#22A884', '#2A788E', '#414487', '#440154'],
    score: ['#2166AC', '#92C5DE', '#DDDDDD', '#FDB863', '#E08214', '#B35806', '#7F3B08'],
    // regime colours that stay apart for colour-blind viewers (Okabe and Ito's palette)
    regimes: ['#E69F00', '#56B4E9', '#009E73', '#F0E442', '#0072B2', '#D55E00', '#CC79A7', '#999999'],
  },
}

const SCORE_EDGES = [
  { upTo: -1.5, label: 'far less' },
  { upTo: -0.5, label: 'less' },
  { upTo: 0.5, label: 'usual' },
  { upTo: 1.5, label: 'more' },
  { upTo: 2.5, label: '' },
  { upTo: 3.5, label: '' },
  { upTo: Infinity, label: 'far more' },
]

let palette = 'fire'
export let FIRE_RAMP = PALETTES.fire.ramp
export let SCORE_STEPS = SCORE_EDGES.map((step, i) => ({ ...step, color: PALETTES.fire.score[i] }))

export function setPalette(name) {
  palette = PALETTES[name] ? name : 'fire'
  FIRE_RAMP = PALETTES[palette].ramp
  SCORE_STEPS = SCORE_EDGES.map((step, i) => ({ ...step, color: PALETTES[palette].score[i] }))
}

// The colour of a fire regime. Normally the colour saved in the data file;
// with the colour-blind palette, one picked by the regime's number instead.
export function regimeColor(id, fromFile) {
  const list = PALETTES[palette].regimes
  if (list) return list[(Math.max(1, Number(id) || 1) - 1) % list.length]
  return fromFile || '#999'
}

export function scoreColor(z) {
  for (const step of SCORE_STEPS) if (z < step.upTo) return step.color
  return SCORE_STEPS[SCORE_STEPS.length - 1].color
}

// Round a number to something tidy like 1, 2, 5, 10, 20, 50, ...
function tidy(v) {
  if (v <= 1) return 1
  const power = Math.pow(10, Math.floor(Math.log10(v)))
  const lead = v / power
  return (lead < 1.5 ? 1 : lead < 3.5 ? 2 : lead < 7.5 ? 5 : 10) * power
}

// Picks the lower edge of each colour class from the values themselves, so
// that every colour gets used: the palest class holds the quietest third of
// the squares, the darkest class only the top few percent.
// (Fire numbers are very lopsided. With equal-width classes almost everything
// would land in the palest colour.)
// Returns something like [1, 20, 100, 500, 2000, 10000].
const CLASS_STARTS = [0, 0.3, 0.55, 0.75, 0.9, 0.97]

export function fireBreaks(values) {
  const sorted = Array.from(values).filter(v => v > 0).sort((a, b) => a - b)
  if (!sorted.length) return [1]
  const breaks = []
  for (const q of CLASS_STARTS) {
    const edge = tidy(sorted[Math.floor(q * (sorted.length - 1))])
    if (!breaks.length || edge > breaks[breaks.length - 1]) breaks.push(edge)
  }
  breaks[0] = Math.min(breaks[0], tidy(sorted[0]))
  return breaks
}

export function fireColor(v, breaks) {
  if (!(v > 0)) return null
  let i = 0
  while (i + 1 < breaks.length && v >= breaks[i + 1]) i++
  // spread the classes across the whole ramp when there are fewer breaks than colours
  const step = breaks.length > 1 ? (FIRE_RAMP.length - 1) / (breaks.length - 1) : 0
  return FIRE_RAMP[Math.round(i * step)]
}

// Is this colour dark enough to need light text or a light marker on top?
export function isDark(hex) {
  const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16)
  return 0.299 * r + 0.587 * g + 0.114 * b < 140
}
