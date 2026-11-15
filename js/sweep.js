// Map timeline sweep: plays every month of the selected years as sound while the map follows.
// (The Listen button opens Mridul's listening panel in sound.js.)
//
// - a soft low hum is always there: that is "a usual month"
// - each month with fire plays a note: more unusual = higher pitch, more fire = louder
// - unusual months also crackle
//
// This is a simple first version. Mridul's sound layer can replace this file;
// keep the same two functions (playCalendar, stopCalendar) and nothing else breaks.

import { CONFIG } from './config.js'

let audio = null
let timer = null
let hum = null

export function isPlaying() { return timer !== null }

export function stopCalendar() {
  if (timer !== null) { clearTimeout(timer); timer = null }
  if (hum) {
    try { hum.gain.gain.setTargetAtTime(0, audio.currentTime, 0.05); hum.osc.stop(audio.currentTime + 0.3) } catch (err) { /* already stopped */ }
    hum = null
  }
}

function note(frequency, loudness, length) {
  const osc = audio.createOscillator()
  const gain = audio.createGain()
  osc.type = 'sine'
  osc.frequency.value = frequency
  gain.gain.setValueAtTime(0, audio.currentTime)
  gain.gain.linearRampToValueAtTime(loudness, audio.currentTime + 0.012)
  gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + length)
  osc.connect(gain).connect(audio.destination)
  osc.start()
  osc.stop(audio.currentTime + length + 0.02)
}

// A soft "ember pop" for unusual months: a few short, muffled pops through a
// low-pass filter, kept quiet (the old version was raw white-noise clicks and sounded harsh).
function crackle(loudness) {
  const length = 0.28
  const buffer = audio.createBuffer(1, Math.floor(audio.sampleRate * length), audio.sampleRate)
  const samples = buffer.getChannelData(0)
  const pops = 3 + Math.floor(Math.random() * 3)
  for (let p = 0; p < pops; p++) {
    const start = Math.floor(Math.random() * samples.length * 0.8)
    const size = Math.floor(audio.sampleRate * 0.012)
    for (let i = 0; i < size && start + i < samples.length; i++) {
      samples[start + i] += (Math.random() * 2 - 1) * Math.exp(-i / (size / 4))   // each pop dies away fast
    }
  }
  const source = audio.createBufferSource()
  const filter = audio.createBiquadFilter()
  filter.type = 'lowpass'
  filter.frequency.value = 1400
  const gain = audio.createGain()
  gain.gain.value = Math.min(0.09, loudness * 0.12)
  source.buffer = buffer
  source.connect(filter).connect(gain).connect(audio.destination)
  source.start()
}

// onStep(row, month) is called as each month plays; onDone() at the end.
export function playCalendar(table, onStep, onDone) {
  stopCalendar()
  audio = audio || new (window.AudioContext || window.webkitAudioContext)()
  if (audio.state === 'suspended') audio.resume()

  // only play the months of the year that ever have fire, to keep it short
  const months = []
  for (let m = 0; m < 12; m++) if (table.usual[m].max > 0) months.push(m)
  const steps = []
  table.years.forEach((_, row) => {
    for (const m of months) if (!Number.isNaN(table.value[row][m])) steps.push([row, m])
  })
  if (!steps.length) { onDone(); return }

  const osc = audio.createOscillator(), gain = audio.createGain()
  osc.frequency.value = 98
  gain.gain.value = 0.035
  osc.connect(gain).connect(audio.destination)
  osc.start()
  hum = { osc, gain }

  const stepTime = 95   // milliseconds per month
  let i = 0
  const next = () => {
    if (i >= steps.length) { stopCalendar(); onStep(null, null); onDone(); return }
    const [row, m] = steps[i++]
    const v = table.value[row][m], z = table.score[row][m]
    onStep(row, m)
    if (v > 0) {
      const size = Math.log10(1 + v) / Math.log10(1 + table.max)          // 0..1
      const pitch = 196 * Math.pow(2, Math.max(-0.5, Math.min(4.5, z)) / 2.2)
      note(pitch, 0.04 + 0.16 * size, 0.22)
      if (z >= CONFIG.unusualSigma) crackle(0.35 + 0.4 * size)
    }
    timer = setTimeout(next, stepTime)
  }
  timer = setTimeout(next, 0)
}
