const MONTHS = ['january','february','march','april','may','june','july','august','september','october','november','december']
const small = ['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen']
const tens = ['','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety']
const words = n => n<20 ? small[n] : tens[Math.floor(n/10)]+(n%10 ? ' '+small[n%10] : '')
export function parseVoiceCommand(transcript) {
  let text=String(transcript).toLowerCase().replace(/[^a-z0-9\s]/g,' ').replace(/\s+/g,' ').trim().replace(/\bacitivty\b/g,'activity')
  // Recognizers may return either digits or words for a spoken year.
  for(let n=99;n>=0;n--) {
    const forms=n===0 ? ['two thousand','twenty hundred'] : [`two thousand and ${words(n)}`,`two thousand ${words(n)}`,`twenty ${n<10?'oh '+words(n):words(n)}`]
    for(const form of forms) text=text.replace(new RegExp(`\\b${form}\\b`,'g'),String(2000+n))
  }
  if(text==='leave')return {type:'leave'}
  if(text==='keyword guide')return {type:'keywords'}
  if(text==='sound guide')return {type:'guide'}
  const speed=/^(slow|normal|fast)$/.exec(text)
  if(speed)return {type:'speed',speed:({slow:.75,normal:1,fast:1.5})[speed[1]]}
  const yearAction=/^(\d{4}) (season overview|strongest ano)$/.exec(text)
  if(yearAction)return {type:yearAction[2]==='season overview'?'summary':'anomaly',year:Number(yearAction[1])}
  const match=new RegExp(`^(?:(${MONTHS.join('|')}) )?(\\d{4})(?: (activity|unusual activity|fire energy|fire energy unusual))?$`).exec(text)
  if(!match)return null
  return {type:'play',year:Number(match[2]),month:match[1]?MONTHS.indexOf(match[1]):null,mode:['unusual activity','fire energy unusual'].includes(match[3])?'anomaly':'activity',metric:match[3]?.startsWith('fire energy')?'frp':'count'}
}

// One click, one utterance. Speak results only after recognition has ended.
export function attachMicrophone({button,status,onStart,onCommand,onMessage,Recognition=window.SpeechRecognition || window.webkitSpeechRecognition}) {
  let active=null, watchdog=null
  function reset() {clearTimeout(watchdog);button.setAttribute('aria-pressed','false');button.classList.remove('is-recording')}
  function cancel() { if(active){const rec=active;active=null;rec.abort()} reset() }
  if(!Recognition || !('speechSynthesis' in window)) {
    button.disabled=true;status.textContent='Voice commands are unavailable in this browser. Open this website in Chrome and use the regular Listen controls if needed.'
    return {cancel,supported:false}
  }
  button.onclick=()=>{
    cancel()
    try { onStart() } catch(error) {
      console.error('Microphone setup failed:',error)
      status.textContent='Could not start Speak. Refresh the page and try again.'
      Promise.resolve().then(()=>onMessage(status.textContent)).catch(error=>console.error('Voice error message failed:',error))
      return
    }
    const rec=new Recognition();active=rec
    let transcript=null, problem=null, finished=false
    rec.lang='en-US';rec.continuous=false;rec.interimResults=false;rec.maxAlternatives=1
    status.textContent='Requesting microphone. Allow access if your browser asks.'
    button.setAttribute('aria-pressed','true');button.classList.add('is-recording')
    const finish=()=>{
      if(finished || active!==rec)return
      finished=true;active=null;reset()
      if(problem) {status.textContent=problem;onMessage(problem);return}
      const command=transcript===null?null:parseVoiceCommand(transcript)
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
    rec.onresult=event=>{if(active!==rec)return;transcript=event.results[0][0].transcript;rec.stop()}
    rec.onnomatch=()=>{rec.stop()}
    rec.onerror=event=>{
      if(active!==rec)return
      if(event.error==='not-allowed' || event.error==='service-not-allowed')problem='Microphone access was not allowed. Allow microphone access in your browser, then try again.'
      else if(event.error==='audio-capture')problem='No microphone was found. Connect a microphone and try again.'
      else if(event.error==='network')problem='Speech recognition could not connect. Check your internet connection and try again.'
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
