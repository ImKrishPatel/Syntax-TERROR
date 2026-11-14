// One small floating box shared by the calendar and the charts.

let box = null

function getBox() {
  if (!box) {
    box = document.createElement('div')
    box.className = 'tip'
    box.setAttribute('role', 'tooltip')
    document.body.appendChild(box)
  }
  return box
}

// Show the tip above an element, or at a point { x, y } on the page.
export function showTip(html, target) {
  const tip = getBox()
  tip.innerHTML = html
  tip.classList.add('is-visible')
  let x, y
  if (target instanceof Element) {
    const r = target.getBoundingClientRect()
    x = r.left + r.width / 2
    y = r.top
  } else {
    x = target.x; y = target.y
  }
  const w = tip.offsetWidth, h = tip.offsetHeight
  const left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2))
  let top = y - h - 10
  if (top < 8) top = y + 26
  tip.style.left = left + 'px'
  tip.style.top = top + 'px'
}

export function hideTip() {
  if (box) box.classList.remove('is-visible')
}
