// Plain-English verdict for the selected area and dates, e.g.
// "Aug 2023 was the 2nd most intense fire month on record here (+2.8σ)".
import { useMemo } from 'react'
import { useSelection } from '../state/selection.js'
import { useHotspots } from '../data/hotspots.js'
import { computeAnomaly } from '../data/anomaly.js'

// 1 -> '', 2 -> '2nd ', 3 -> '3rd ', 11 -> '11th '
function ordinal(n) {
  if (n === 1) return ''
  const tens = n % 100
  const suffix = tens >= 11 && tens <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th'
  return `${n}${suffix} `
}

function describe(z) {
  if (z >= 2) return 'far above normal'
  if (z >= 1) return 'above normal'
  if (z > -1) return 'close to normal'
  return 'below normal'
}

export default function VerdictCard() {
  const { area, from, to } = useSelection()
  const { records, loading } = useHotspots()
  const a = useMemo(() => computeAnomaly(records, area, from, to), [records, area, from, to])

  if (loading) return null
  if (!a) {
    return (
      <section className="card verdict">
        <h2>Verdict</h2>
        <p className="verdict-text">No hotspots were recorded here in this period.</p>
      </section>
    )
  }

  const sigma = `${a.zScore >= 0 ? '+' : '−'}${Math.abs(a.zScore).toFixed(1)}σ`
  const level = a.zScore >= 2 ? 'high' : a.zScore >= 1 ? 'raised' : 'normal'

  return (
    <section className={`card verdict verdict-${level}`}>
      <h2>Verdict</h2>
      <p className="verdict-text">
        {a.monthLabel} was the {ordinal(a.rank)}most intense fire month on record here ({sigma}).
      </p>
      <p className="hint">
        {Math.round(a.hotspots).toLocaleString()} hotspots, against {Math.round(a.typical).toLocaleString()} in a
        typical {a.monthName} ({a.firstYear} to {a.lastYear}). That is {describe(a.zScore)}.
      </p>
      {a.isEstimated && (
        <p className="hint">This month is before 2012, so its value is a model estimate, not a VIIRS observation.</p>
      )}
    </section>
  )
}
