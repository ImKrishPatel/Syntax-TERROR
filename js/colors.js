// Colour ramps. Both were picked to stay readable for colour-blind viewers:
// the fire ramp gets steadily darker, and the "compared with usual" ramp
// goes blue (less than usual) to grey (usual) to orange-red (more than usual).

export const FIRE_RAMP = ['#F8DC78', '#F4B544', '#EB872B', '#D6531F', '#AC281C', '#6B1215']

export const SCORE_STEPS = [
  { upTo: -1.5, color: '#2C6A99', label: 'far less' },
  { upTo: -0.5, color: '#8DB3CC', label: 'less' },
  { upTo: 0.5, color: '#D5DAD2', label: 'usual' },
  { upTo: 1.5, color: '#F2BE74', label: 'more' },
  { upTo: 2.5, color: '#E2862D', label: '' },
  { upTo: 3.5, color: '#BC471E', label: '' },
  { upTo: Infinity, color: '#761915', label: 'far more' },
]

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
