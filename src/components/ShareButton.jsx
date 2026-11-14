// Copies the current URL (which contains the area + dates) to the clipboard.
import { useState } from 'react'

export default function ShareButton() {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href)
    } catch {
      window.prompt('Copy this link:', window.location.href)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <button className="share" onClick={copy}>
      {copied ? 'Link copied' : 'Copy share link'}
    </button>
  )
}
