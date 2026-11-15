import { useState } from 'react'
import './Chatbot.css'

export default function Chatbot() {
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)

  async function sendMessage(e) {
    e.preventDefault()

    const text = input.trim()
    if (!text || loading) return

    const updatedMessages = [
      ...messages,
      { role: 'user', content: text },
    ]

    setMessages(updatedMessages)
    setInput('')
    setLoading(true)

    try {
      const response = await fetch('http://127.0.0.1:8000/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: updatedMessages }),
      })

      if (!response.ok) {
        throw new Error('The assistant could not respond.')
      }

      const data = await response.json()

      setMessages([
        ...updatedMessages,
        { role: 'model', content: data.reply },
      ])
    } catch (error) {
      setMessages([
        ...updatedMessages,
        {
          role: 'model',
          content: 'Sorry, I could not connect to the assistant. Please check that the backend server is running.',
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="chatbot">
      {isOpen && (
        <section className="chat-window">
          <div className="chat-header">
            <div>
              <strong>Fire Assistant</strong>
              <span>Ask me anything</span>
            </div>
            <button onClick={() => setIsOpen(false)} aria-label="Close chat">
              ×
            </button>
          </div>

          <div className="chat-messages">
            {messages.length === 0 && (
              <div className="chat-welcome">
                Hi! I'm your AI assistant. Ask me anything or get help
                exploring the Fire Hotspot Explorer.
              </div>
            )}

            {messages.map((message, index) => (
              <div
                key={index}
                className={`chat-message ${message.role}`}
              >
                {message.content}
              </div>
            ))}

            {loading && (
              <div className="chat-message model">Thinking...</div>
            )}
          </div>

          <form className="chat-input" onSubmit={sendMessage}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type your message..."
              disabled={loading}
            />
            <button type="submit" disabled={loading || !input.trim()}>
              Send
            </button>
          </form>
        </section>
      )}

      <button
        className="chat-toggle"
        onClick={() => setIsOpen(!isOpen)}
        aria-label={isOpen ? 'Close chatbot' : 'Open chatbot'}
      >
        {isOpen ? '×' : '💬'}
      </button>
    </div>
  )
}
