import { MONTHS } from './config.js'
export function listeningMonths(table, year, hasCoverage = true) {
  const row = table.years.indexOf(year)
  if (row < 0) return []
  return table.value[row].map((value, month) => {
    const score = table.score[row][month], usual = table.usual[month]
    const present = hasCoverage && Number.isFinite(value)
    const comparable = present && usual.years >= 2 && usual.sd > 0 && Number.isFinite(score)
    return { row, month, year, activity: present ? value : null, status: present ? 'complete' : 'missing',
      anomaly: !comparable ? null : score >= 0.5 ? 'above' : score < -0.5 ? 'below' : 'typical',
      score: comparable ? score : null, estimated: table.estimatedShare[row][month] >= 0.5,
      baseline: usual.mean, metric: table.metric }
  })
}
export function describeMonth(m) {
  const name = `${MONTHS[m.month]} ${m.year}.`
  if (m.status === 'missing') return `${name} No data available. This does not mean zero activity.`
  const amount = m.activity.toLocaleString('en-CA', { maximumFractionDigits: 1 })
  const unit = m.metric === 'frp' ? 'megawatts of summed fire radiative power' : 'hotspots'
  const comparison = ({above:'Above usual activity',below:'Below usual activity',typical:'Typical activity'})[m.anomaly] || 'Historical comparison unavailable'
  return `${name} ${amount} ${unit}. ${comparison}.${m.estimated ? ' Modeled estimate from MODIS.' : ''}`
}
export function strongestMonth(months) {
  return months.filter(m=>m.score !== null && m.score >= 2 && m.activity > 0)
    .reduce((best,m)=>!best || m.score > best.score ? m : best,null)
}
export function seasonSummary(months) {
  const known = months.filter(m=>m.status !== 'missing')
  if (!known.length) return 'No data available for this area and year.'
  const peak = known.reduce((a,b)=>a.activity >= b.activity ? a:b)
  const unusual = known.filter(m=>m.score !== null && m.score >= 2 && m.activity>0).length
  const missing = months.length-known.length
  return `${peak.year}. ${peak.activity === 0 ? 'No activity is recorded in the available months.' : `${MONTHS[peak.month]} had the highest recorded activity.`} ${unusual === 0 ? 'No month was' : unusual === 1 ? 'One month was' : `${unusual} months were`} at least two standard deviations above usual.${missing ? ` ${missing === 1 ? 'One month has' : `${missing} months have`} no data so far; this is an incomplete year.` : ''}${known.some(m=>m.estimated) ? ' Some values are modeled estimates from MODIS.' : ''}`
}
