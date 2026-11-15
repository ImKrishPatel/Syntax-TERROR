// One cancellable audio session; tones are generated locally, with no audio assets.
export function createPlayer() {
  let context, generation = 0, active = [], timers = new Map();
  const sleep = ms => new Promise(resolve => {
    const id = setTimeout(() => { timers.delete(id); resolve(); }, ms);
    timers.set(id, resolve);
  });
  function stop() {
    generation++;
    for (const node of active) { try { node.stop(); } catch {} }
    active = [];
    for (const [id, resolve] of timers) { clearTimeout(id); resolve(); }
    timers.clear();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  }
  async function ready() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) throw new Error('Audio playback is unavailable in this browser. Month descriptions remain available below.');
    context ||= new Audio();
    await context.resume();
  }
  async function speak(text, volume, token, rate = 1) {
    if (!('speechSynthesis' in window) || token !== generation) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-US'; utterance.volume = volume; utterance.rate = rate;
    // Bound waiting if a browser fails to deliver speech events.
    await new Promise(resolve => {
      const id = setTimeout(() => { timers.delete(id); window.speechSynthesis.cancel(); resolve(); }, 18000);
      const finish = () => { clearTimeout(id); timers.delete(id); resolve(); };
      timers.set(id, resolve); utterance.onend = finish; utterance.onerror = finish;
      window.speechSynthesis.speak(utterance);
    });
  }
  function tone(frequency, duration, volume) {
    const osc = context.createOscillator(), gain = context.createGain();
    const now = context.currentTime;
    osc.type = 'sine'; osc.frequency.value = frequency;
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(volume * 0.16, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, now + duration);
    osc.connect(gain); gain.connect(context.destination);
    active.push(osc); osc.start(); osc.stop(now + duration + 0.02);
    osc.onended = () => { osc.disconnect(); gain.disconnect(); active = active.filter(x => x !== osc); };
  }
  async function play({text, frequencies, speech, volume, speed, sentencePauseMs = 0, speechRate = 1}) {
    const token = generation;
    await ready();
    if (token !== generation) return false;
    if (speech) {
      const sentences = sentencePauseMs > 0
        ? (text.match(/[^.;]+[.;]|[^.;]+$/g) || []).map(part => part.trim()).filter(Boolean)
        : [text];
      for (let i = 0; i < sentences.length; i++) {
        if (token !== generation) return false;
        await speak(sentences[i], volume, token, speechRate);
        if (token !== generation) return false;
        // Keep the summary gap independent of playback speed.
        if (sentencePauseMs > 0 && /[.;]$/.test(sentences[i])) await sleep(sentencePauseMs);
      }
    }
    for (const frequency of frequencies) {
      if (token !== generation) return false;
      tone(frequency, 0.16 / speed, volume);
      await sleep(300 / speed);
    }
    await sleep(450 / speed);
    return token === generation;
  }
  return {play, stop, ready, dispose() { stop(); if (context) context.close(); }};
}
export function soundFor(month, mode, max) {
  if (month.status === 'missing' || !Number.isFinite(month.activity)) return [180,180];
  if (mode === 'anomaly') {
    if (month.status === 'partial' || !month.anomaly) return [220,220];
    return ({above:[330,440,550], below:[550,440,330], typical:[440,440]})[month.anomaly] || [220,220];
  }
  if (month.activity === 0) return [260];
  return Array.from({length:Math.max(1,Math.min(8,Math.ceil(month.activity / Math.max(1,max) * 8)))},()=>440);
}
