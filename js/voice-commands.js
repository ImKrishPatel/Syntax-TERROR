const MONTHS = ['january','february','march','april','may','june','july','august','september','october','november','december']
const small = ['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen']
const tens = ['','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety']
const words = n => n<20 ? small[n] : tens[Math.floor(n/10)]+(n%10 ? ' '+small[n%10] : '')
// Decode only fully spelled segments; normal multi-word place names stay intact.
export function decodeSpelledPlace(text) {
  const letterNames={ay:'a',bee:'b',be:'b',see:'c',sea:'c',dee:'d',ee:'e',eff:'f',gee:'g',aitch:'h',eye:'i',jay:'j',kay:'k',el:'l',ell:'l',em:'m',en:'n',oh:'o',pee:'p',cue:'q',queue:'q',are:'r',ar:'r',ess:'s',tee:'t',you:'u',vee:'v',ex:'x',why:'y',zee:'z',zed:'z'}
  const decode=part=>{
    if(/\bspace\b/.test(part))return part.split(/\bspace\b/).map(decode).join(' ')
    const tokens=part.trim().replace(/double u/g,'w').split(/\s+/)
    if(tokens.length>1 && tokens.every(t=>/^[a-z]$/.test(t) || letterNames[t]))return tokens.map(t=>letterNames[t] || t).join('')
    return part.trim()
  }
  return text.split(/\bpause\b/).map(decode).filter(Boolean).join(' ')
}
export function parseVoiceCommand(transcript) {
  let text=String(transcript).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9\s]/g,' ').replace(/\s+/g,' ').trim().replace(/\bacitivty\b/g,'activity')
  text=text.replace(/^please /,'').replace(/ please$/,'')
  // Recognizers may return either digits or words for a spoken year.
  for(let n=99;n>=0;n--) {
    const forms=n===0 ? ['two thousand','twenty hundred'] : [`two thousand and ${words(n)}`,`two thousand ${words(n)}`,`twenty ${n<10?'oh '+words(n):words(n)}`]
    for(const form of forms) text=text.replace(new RegExp(`\\b${form}\\b`,'g'),String(2000+n))
  }
  const selection=/^(?:choose|select|country) (.+)$/.exec(text)
  if(selection)return {type:'choosePlace',choice:decodeSpelledPlace(selection[1])}
  if(['yes','yes please','confirm'].includes(text))return {type:'confirmPlace',accept:true}
  if(['no','no thanks','cancel search'].includes(text))return {type:'confirmPlace',accept:false}
  if(text==='leave')return {type:'leave'}
  if(text==='keyword guide')return {type:'keywords'}
  if(text==='sound guide')return {type:'guide'}
  const speed=/^(slow|normal|fast)$/.exec(text)
  if(speed)return {type:'speed',speed:({slow:.75,normal:1,fast:1.5})[speed[1]]}
  const yearAction=/^(\d{4}) (season overview|strongest ano)$/.exec(text)
  if(yearAction)return {type:yearAction[2]==='season overview'?'summary':'anomaly',year:Number(yearAction[1])}
  const period=/^(?:(?:time period|date range|years|from) )?(\d{4})(?: to (\d{4}))?$/.exec(text)
  // Bare single years retain the existing annual audio command.
  if(period && (period[2] || /^(time period|date range|years|from) /.test(text)))
    return {type:'navigate',place:null,fromYear:Number(period[1]),toYear:Number(period[2] || period[1])}
  const combined=/^(?:(?:search(?: for)?|find|show(?: me)?|go to) )?(.+?) (?:from |for )?(\d{4}) to (\d{4})$/.exec(text)
  if(combined)return {type:'navigate',place:decodeSpelledPlace(combined[1]),fromYear:Number(combined[2]),toYear:Number(combined[3])}
  const place=/^(?:search(?: for)?|find|go to|show(?: me)?|take me to|look up) (.+)$/.exec(text)
  if(place)return {type:'navigate',place:decodeSpelledPlace(place[1]),fromYear:null,toYear:null}
  const match=new RegExp(`^(?:(${MONTHS.join('|')}) )?(\\d{4})(?: (activity|unusual activity|fire energy|fire energy unusual))?$`).exec(text)
  if(!match)return null
  return {type:'play',year:Number(match[2]),month:match[1]?MONTHS.indexOf(match[1]):null,mode:['unusual activity','fire energy unusual'].includes(match[3])?'anomaly':'activity',metric:match[3]?.startsWith('fire energy')?'frp':'count'}
}

// One click, one utterance. Speak results only after recognition has ended.
export function attachMicrophone({button,status,onStart,onCommand,onMessage,Recognition=window.SpeechRecognition || window.webkitSpeechRecognition}) {
  let active=null, watchdog=null, localModelReady=false
  // Prefer installed on-device recognition when the browser supports it.
  // Never download a model or change browser settings automatically.
  if(typeof Recognition?.available === 'function' && 'processLocally' in Recognition.prototype) {
    Promise.resolve().then(()=>Recognition.available({langs:['en-US'],processLocally:true}))
      .then(result=>{localModelReady=result==='available'}).catch(()=>{})
  }
  function reset() {clearTimeout(watchdog);button.setAttribute('aria-pressed','false');button.classList.remove('is-recording')}
  function cancel() { if(active){const rec=active;active=null;rec.abort()} reset() }
  if(!Recognition || !('speechSynthesis' in window)) {
    button.disabled=true;status.textContent='Voice commands are unavailable in this browser. Open this website in Chrome and use the regular Listen controls if needed.'
    return {cancel,supported:false}
  }
  button.onclick=()=>{
    cancel()
    if(window.isSecureContext===false) {
      const message='Speak needs a secure website address. Open the HTTPS website, or use localhost for a local preview.'
      status.textContent=message;onMessage(message);return
    }
    try { onStart() } catch(error) {
      console.error('Microphone setup failed:',error)
      status.textContent='Could not start Speak. Refresh the page and try again.'
      Promise.resolve().then(()=>onMessage(status.textContent)).catch(error=>console.error('Voice error message failed:',error))
      return
    }
    const rec=new Recognition();active=rec
    let transcript=null, alternatives=[], problem=null, finished=false
    if(localModelReady) rec.processLocally=true
    rec.lang='en-US';rec.continuous=false;rec.interimResults=false;rec.maxAlternatives=5
    status.textContent='Requesting microphone. Allow access if your browser asks.'
    button.setAttribute('aria-pressed','true');button.classList.add('is-recording')
    const finish=()=>{
      if(finished || active!==rec)return
      finished=true;active=null;reset()
      if(problem) {status.textContent=problem;onMessage(problem);return}
      const command=transcript===null?null:parseVoiceCommand(transcript)
      if(command?.type==='navigate' && command.place) {
        command.placeAlternatives=alternatives.map(parseVoiceCommand).filter(c=>c?.type==='navigate' && c.place && c.fromYear===command.fromYear && c.toYear===command.toYear).map(c=>c.place).filter(p=>p!==command.place)
      }
      status.textContent=transcript ? `Heard: “${transcript}”` : 'No command heard.'
      if(command) {
        // Recognition success does not guarantee playback success. Surface both
        // synchronous integration errors and rejected async command handlers.
        const failed=error=>{
          console.error('Voice command playback failed:',error)
          const message='Your command was recognized, but playback could not start. Refresh the page and try again.'
          status.textContent=message
          Promise.resolve().then(()=>onMessage(message)).catch(error=>console.error('Voice error message failed:',error))
        }
        try {Promise.resolve(onCommand(command)).catch(failed)} catch(error) {failed(error)}
      }
      else {status.textContent+=' Try again with right keyword';onMessage('Try again with right keyword')}
    }
    rec.onstart=()=>{if(active===rec)status.textContent='Listening… say one command.'}
    rec.onresult=event=>{if(active!==rec)return;alternatives=Array.from(event.results[0]).slice(0,5).map(result=>result.transcript);transcript=alternatives[0];rec.stop()}
    rec.onnomatch=()=>{rec.stop()}
    rec.onerror=event=>{
      if(active!==rec)return
      if(event.error==='not-allowed' || event.error==='service-not-allowed')problem='Microphone access was not allowed. Allow microphone access in your browser, then try again.'
      else if(event.error==='audio-capture')problem='No microphone was found. Connect a microphone and try again.'
      else if(event.error==='network')problem='The browser could not reach its speech recognition service. Try this website in Google Chrome. If you are already using Chrome, check whether your network or VPN blocks speech recognition. The regular Listen controls still work.'
      else if(event.error!=='no-speech' && event.error!=='aborted')problem='Speech recognition is unavailable. Please try again.'
      finish()
    }
    rec.onend=finish
    try {rec.start();watchdog=setTimeout(()=>{if(active===rec){rec.abort();finish()}},20000)}
    catch {problem='Could not start the microphone. Please try again.';finish()}
  }
  window.addEventListener('keydown',event=>{
    // Use the physical key because Option changes the character on macOS.
    if(event.code!=='KeyM' || !event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey)return
    if(event.defaultPrevented || event.repeat || event.isComposing || button.disabled || active)return
    event.preventDefault()
    button.click()
  })
  window.addEventListener('pagehide',cancel)
  return {cancel,supported:true}
}

export const KEYWORD_GUIDE = [
  ['Yes / No', 'confirm or reject a suggested place after clicking Speak again'],
  ['Search C A L G A R Y Pause C A N A D A', 'spell a place and optional country; say the word Pause'],
  ['Choose Canada', 'select a country from the spoken place matches'],
  ['Choose one', 'select a numbered place match'],
  ['Search Calgary', 'select a place; replace Calgary with your place'],
  ['Time period 2008 to 2022', 'set the years; use 2006 through 2026'],
  ['Calgary from 2008 to 2022', 'select a place and its time period'],
  ['Time period 2024', 'select one year'],
  ['Year Activity', 'a year’s hotspot activity'],
  ['Year Unusual Activity', 'a year’s unusual hotspot activity'],
  ['Month Year Activity', 'one month’s hotspot activity'],
  ['Month Year Unusual Activity', 'one month’s unusual hotspot activity'],
  ['Year Fire Energy', 'a year’s fire radiative power'],
  ['Year Fire Energy Unusual', 'a year’s unusual fire radiative power'],
  ['Month Year Fire Energy', 'one month’s fire radiative power'],
  ['Month Year Fire Energy Unusual', 'one month’s unusual fire radiative power'],
  ['Slow', 'slow playback at 0.75 times speed'],
  ['Normal', 'normal playback at 1 times speed'],
  ['Fast', 'fast playback at 1.5 times speed'],
  ['Year Season Overview', 'a spoken year summary'],
  ['Year Strongest Ano', 'the strongest qualifying anomaly’s description and tones'],
  ['Sound Guide', 'all sound examples in order'],
  ['Keyword Guide', 'all keywords and their meanings'],
  ['Leave', 'cancel the microphone action'],
  ['Year', 'a year’s hotspot activity'],
]
