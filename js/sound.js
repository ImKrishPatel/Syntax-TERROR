// Integrated listening panel. Uses the same table, metric and selection as the map/calendar.
import { createPlayer, soundFor } from './fire-audio.js'
import { listeningMonths, describeMonth, seasonSummary, strongestMonth } from './listening-model.js'
import { MONTHS } from './config.js'
import { attachMicrophone, KEYWORD_GUIDE } from './voice-commands.js?v=pearl-voice-4'

export function createListeningPanel({ onPick, onHighlight, onMode, onMetric, onOpen, onVoiceStart, audioPlayer = createPlayer(), microphoneFactory = attachMicrophone }) {
  const $ = id => document.getElementById(id)
  const player = audioPlayer
  let table, context = '', covered = true, months = [], year, index = 0, token = 0, running = false
  let beforeMicrophone = null
  const supported = 'speechSynthesis' in window
  $('audioSpeech').disabled = $('audioSummary').disabled = !supported
  if (!supported) $('audioSupport').textContent = 'Speech is unavailable in this browser. Text descriptions and tones are still available.'
  const settings = () => ({speed:Number($('audioSpeed').value), volume:$('audioMute').checked ? 0 : Number($('audioVolume').value), speech:$('audioSpeech').checked})
  function stop() {
    token++; player.stop(); running = false
    $('verdict').setAttribute('aria-live','polite')
    $('audioPlay').textContent = 'Play from here'
    $('audioPlay').setAttribute('aria-pressed','false')
    onHighlight(null,null)
    $('audioState').textContent = 'Ready to explore'
  }
  function redraw() {
    const m = months[index]
    if (!m) return
    $('audioYear').value = year
    $('audioMonth').textContent = `${MONTHS[m.month]} ${year}`
    $('audioDescription').textContent = describeMonth(m)
    $('audioSummaryText').textContent = seasonSummary(months)
    $('audioProgress').value = index + 1
    $('audioProgress').setAttribute('aria-valuetext',`${MONTHS[m.month]} ${year}`)
    $('audioPrevious').disabled = index===0
    $('audioNext').disabled = index===11
    $('audioAnomaly').disabled = !strongestMonth(months)
    $('audioBaseline').textContent = `Baseline: the same calendar month across ${table.years[0]}–${table.years.at(-1)}. Sound intensity is scaled within ${year}.`
    $('audioPlay').disabled = $('audioReplay').disabled = !covered
  }
  function select(i) {
    stop(); index=i; redraw(); onPick(year,index)
  }
  async function play(single = false) {
    stop(); $('audioError').textContent=''; running=true; const mine=token
    $('audioPlay').textContent='Pause'; $('audioPlay').setAttribute('aria-pressed','true')
    $('audioState').textContent='Playing timeline'
    $('verdict').setAttribute('aria-live','off')
    const set=settings(), mode=$('audioMode').value
    const max = Math.max(1,...months.map(m=>m.activity || 0))
    try {
      for(let i=index;i<months.length;i++) {
        if(mine!==token) return
        index=i; redraw(); onPick(year,i); onHighlight(months[i].row,i)
        const ok = await player.play({text:describeMonth(months[i]),frequencies:soundFor(months[i],mode,max),...set})
        if(!ok || mine!==token) return
        if(single) break
      }
    } catch(e) { if(mine===token) $('audioError').textContent=e.message }
    if(mine===token) stop()
  }
  async function speakSummary() {
    stop(); const mine=token; $('audioError').textContent=''
    $('audioState').textContent='Reading summary'
    try { const set=settings(); await player.play({text:seasonSummary(months),frequencies:[],...set,speech:true,speechRate:set.speed,sentencePauseMs:500}) }
    catch(e) { if(mine===token) $('audioError').textContent=e.message }
    if(mine===token) $('audioState').textContent='Ready to explore'
  }
  $('listenButton').onclick=()=>{
    const open=$('audioPanel').hidden
    $('audioPanel').hidden=!open
    $('listenButton').setAttribute('aria-expanded',String(open))
    $('listenText').textContent=open?'Close listening panel':'Listen'
    if(open) { $('audioPanel').scrollIntoView({block:'nearest',behavior:'auto'}); $('audioHeading').focus() }
    else stop()
  }
  $('audioPlay').onclick=()=>running?stop():play()
  $('audioReplay').onclick=()=>play(true)
  $('audioStop').onclick=stop
  $('audioPrevious').onclick=()=>select(Math.max(0,index-1))
  $('audioNext').onclick=()=>select(Math.min(11,index+1))
  $('audioYear').onchange=()=>{
    stop();year=Number($('audioYear').value);months=listeningMonths(table,year,covered);index=0;redraw();onPick(year,0)
  }
  $('audioSummary').onclick=speakSummary
  $('audioAnomaly').onclick=()=>{const peak=strongestMonth(months);if(peak)select(peak.month)}
  $('audioMode').onchange=()=>{stop();onMode($('audioMode').value==='anomaly'?'score':'amount')}
  for(const id of ['audioSpeed','audioVolume','audioMute','audioSpeech']) $(id).onchange=()=>{
    stop();$('audioVolumeText').textContent=`Volume · ${Math.round(Number($('audioVolume').value)*100)}%`
  }
  const examples=[
    ['Low activity',[440]],['High activity',[440,440,440,440,440,440]],['Zero activity',[260]],
    ['Above usual',[330,440,550]],['Below usual',[550,440,330]],['Typical',[440,440]],
    ['Missing data',[180,180]],['Comparison unavailable',[220,220]]
  ]
  for(const [label,frequencies] of examples) {
    const b=document.createElement('button');b.type='button';b.className='button';b.textContent=`Hear: ${label}`
    b.onclick=async()=>{
      stop();const mine=token;$('audioError').textContent='';$('audioState').textContent=`Example: ${label}`
      try {await player.play({text:label,frequencies,...settings()})}catch(e){if(mine===token)$('audioError').textContent=e.message}
      if(mine===token)$('audioState').textContent='Ready to explore'
    }
    $('audioExamples').appendChild(b)
  }
  async function voiceMessage(text) {
    stop(); const mine=token; $('audioError').textContent=''
    try {const set=settings();await player.play({text,frequencies:[],...set,speech:true,speechRate:set.speed})}
    catch(e) {if(mine===token)$('audioError').textContent=e.message}
  }
  async function voiceCommand(command) {
    stop()
    if(command.type==='leave') {
      if(beforeMicrophone) {
        $('audioSpeech').checked=beforeMicrophone.speech
        $('audioMute').checked=beforeMicrophone.muted
        $('audioVolume').value=beforeMicrophone.volume
        $('audioVolumeText').textContent=`Volume · ${Math.round(Number(beforeMicrophone.volume)*100)}%`
      }
      $('voiceStatus').textContent='Microphone action cancelled.'
      return
    }
    $('audioSpeech').checked=true
    // A microphone request explicitly asks for audible output.
    $('audioMute').checked=false
    if(Number($('audioVolume').value)===0)$('audioVolume').value='0.6'
    $('audioVolumeText').textContent=`Volume · ${Math.round(Number($('audioVolume').value)*100)}%`
    if(command.type==='speed') {
      $('audioSpeed').value=String(command.speed)
      await voiceMessage(`Playback speed set to ${command.speed===.75?'slow':command.speed===1?'normal':'fast'}.`)
      return
    }
    if(command.type==='keywords') {
      if(onOpen) onOpen()
      else {
        $('audioPanel').hidden=false
        $('listenButton').setAttribute('aria-expanded','true')
        $('listenText').textContent='Close listening panel'
      }
      $('keywordGuide').open=true
      const mine=token, set=settings()
      try {
        for(const [phrase,brief] of KEYWORD_GUIDE) {
          if(mine!==token)return
          $('audioState').textContent=`Keyword: ${phrase}`
          const ok=await player.play({text:`Speak ${phrase} for ${brief}.`,frequencies:[],...set,speech:true,speechRate:set.speed})
          if(!ok || mine!==token)return
        }
      }catch(e){if(mine===token)$('audioError').textContent=e.message}
      if(mine===token)$('audioState').textContent='Ready to explore'
      return
    }
    if(command.type==='guide') {
      const mine=token; const set=settings()
      try {
        for(const [label,frequencies] of examples) {
          if(mine!==token)return
          $('audioState').textContent=`Example: ${label}`
          const ok=await player.play({text:label,frequencies,...set,speech:true,speechRate:set.speed})
          if(!ok || mine!==token)return
        }
      }catch(e){if(mine===token)$('audioError').textContent=e.message}
      if(mine===token)$('audioState').textContent='Ready to explore'
      return
    }
    if(!table?.years.includes(command.year)) {
      await voiceMessage(`That year is outside the selected time period. Choose a year from ${table.years[0]} to ${table.years.at(-1)}.`);return
    }
    if(!covered){await voiceMessage('No data available for the selected region.');return}
    year=command.year;index=command.month ?? 0;months=listeningMonths(table,year,covered)
    if(command.type==='summary'){redraw();onPick(year,index);await speakSummary();return}
    if(command.type==='anomaly') {
      const peak=strongestMonth(months)
      if(!peak){redraw();await voiceMessage(`No qualifying strongest anomaly is available for ${year}.`);return}
      index=peak.month
    }
    // Select the requested month before syncing mode, so host redraw preserves the requested year.
    redraw();onPick(year,index)
    if(command.type==='play')onMetric(command.metric)
    onMode(command.type==='anomaly' || command.mode==='anomaly'?'score':'amount')
    await play(command.type==='anomaly' || command.month!==null)
  }
  for(const [phrase,brief] of KEYWORD_GUIDE) {
    const item=document.createElement('li')
    item.textContent=`Speak “${phrase}” for ${brief}.`
    $('keywordEntries').appendChild(item)
  }
  const microphone=microphoneFactory({
    button:$('microphoneButton'),status:$('voiceStatus'),
    onStart:()=>{
      onVoiceStart?.()
      beforeMicrophone={speech:$('audioSpeech').checked,muted:$('audioMute').checked,volume:$('audioVolume').value}
      stop();$('audioSpeech').checked=true;$('audioMute').checked=false
      if(Number($('audioVolume').value)===0)$('audioVolume').value='0.6'
      $('audioVolumeText').textContent=`Volume · ${Math.round(Number($('audioVolume').value)*100)}%`
      player.ready().catch(e=>{$('audioError').textContent=e.message})
    },
    onCommand:voiceCommand,onMessage:voiceMessage,
  })
  $('microphoneButton').disabled=true // Enable after the initial data table is ready.
  $('audioStop').onclick=()=>{microphone.cancel();stop()}
  return {
    stop,
    update(nextTable, focus, nextContext, hasCoverage, colorMode) {
      $('microphoneButton').disabled=!supported || microphone.supported===false
      const changed=context!==nextContext
      if(changed) {microphone.cancel();stop()}
      context=nextContext;table=nextTable;covered=hasCoverage
      $('audioMode').value=colorMode==='score'?'anomaly':'activity'
      if(changed) {
        $('audioYear').replaceChildren(...table.years.map(y=>new Option(y,y)))
        year=focus?.year || (table.years.includes(year)?year:table.years.at(-1))
      }
      if(focus) {year=focus.year;index=focus.month}
      else if(changed)index=0
      months=listeningMonths(table,year,covered)
      redraw()
    }
  }
}
