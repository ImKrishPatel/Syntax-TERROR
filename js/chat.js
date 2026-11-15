// The chat box (the round button in the bottom-right corner).
//
// How it works, step by step:
//   1. The user types a question.
//   2. We take a text summary of what is on screen right now (area, years,
//      every month's numbers, regimes, forecast...). app.js builds it.
//   3. We send the summary + the question to our small server
//      (server/chat-worker.js), which asks Google Gemini and sends back the answer.
//   4. We show the answer in the box.
//
// No key is needed here. The Gemini key is kept secret on the server.

import { CONFIG } from './config.js'

const $ = id => document.getElementById(id)

let describeView = null   // function from app.js that returns the screen summary as text
let history = []          // the conversation so far: [{ role: 'user' | 'model', text }]
let busy = false

// ---------------------------------------------------------------------------
// Showing messages
// ---------------------------------------------------------------------------
// turn the answer's simple formatting (**bold**, *italic*, lists, new lines) into HTML,
// escaping everything else so the answer can't inject anything into the page
function toHtml(text) {
  const safe = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return safe
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>')
    .replace(/^\s*[-*] /gm, '• ')
    .replace(/\n/g, '<br>')
}

function addMessage(who, text) {
  const p = document.createElement('p')
  p.className = `chat-msg is-${who}`
  p.innerHTML = toHtml(text)
  $('chatLog').appendChild(p)
  $('chatLog').scrollTop = $('chatLog').scrollHeight
  return p
}

// ---------------------------------------------------------------------------
// Asking the server
// ---------------------------------------------------------------------------
async function askServer(question) {
  if (!CONFIG.chat.url) throw new Error('The chat is not connected yet. Put the server address in chat.url in js/config.js.')

  const messages = [...history.slice(-CONFIG.chat.maxTurns * 2), { role: 'user', text: question }]
  let response
  try {
    response = await fetch(CONFIG.chat.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, data: describeView() }),
      signal: AbortSignal.timeout(80000),   // stop waiting after 80 seconds
    })
  } catch (err) {
    if (err.name === 'TimeoutError') throw new Error('That took too long. Please try again.')
    throw new Error('Could not reach the chat. Check your internet connection and try again.')
  }
  const result = await response.json().catch(() => ({}))
  if (response.status === 429) throw new Error('Lots of questions right now. Wait a minute and try again.')
  if (!response.ok) {
    console.warn('Chat server said:', result)   // details for us, in the browser console (F12)
    throw new Error('Sorry, something went wrong. Please try again.')
  }
  return result.answer
}

async function send(question) {
  question = question.trim()
  if (!question || busy) return
  busy = true
  $('chatSend').disabled = true
  $('chatIdeas').hidden = true
  $('chatInput').value = ''

  addMessage('user', question)
  const waiting = addMessage('bot', 'Thinking…')
  waiting.classList.add('is-waiting')

  try {
    const answer = await askServer(question)
    waiting.remove()
    addMessage('bot', answer)
    history.push({ role: 'user', text: question }, { role: 'model', text: answer })
  } catch (err) {
    waiting.remove()
    addMessage('error', err.message)
  }

  busy = false
  $('chatSend').disabled = false
  $('chatInput').focus()
}

// ---------------------------------------------------------------------------
// Start: app.js calls this once and gives us the summary function
// ---------------------------------------------------------------------------
export function initChat(getSummary) {
  describeView = getSummary
  const panel = $('chatPanel'), openButton = $('chatOpen')

  const open = isOpen => {
    panel.hidden = !isOpen
    openButton.setAttribute('aria-expanded', String(isOpen))
    openButton.hidden = isOpen
    if (isOpen) $('chatInput').focus()
    else openButton.focus()
  }
  openButton.onclick = () => open(true)
  $('chatClose').onclick = () => open(false)
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) open(false)
  })

  $('chatForm').onsubmit = event => {
    event.preventDefault()
    send($('chatInput').value)
  }
  $('chatIdeas').innerHTML = CONFIG.chat.ideas
    .map(q => `<button type="button" class="chat-idea">${q}</button>`).join('')
  $('chatIdeas').querySelectorAll('button').forEach(b => { b.onclick = () => send(b.textContent) })
}
