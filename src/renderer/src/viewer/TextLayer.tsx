import { memo, useEffect, useRef } from 'react'
import type { PageInfo, PageText } from '@shared/types'
import { usePageData } from '@/lib/pageData'

/**
 * Capa de texto invisible sobre la página: permite seleccionar y copiar con el
 * comportamiento nativo del navegador. Cada palabra ocupa exactamente su caja
 * real en el PDF, así que la selección coincide con lo que se ve.
 */

const measurer = document.createElement('canvas').getContext('2d')

function naturalWidth(text: string, size: number): number {
  if (!measurer) return 0
  measurer.font = `${size}px sans-serif`
  return measurer.measureText(text).width
}

function buildLayer(text: PageText): DocumentFragment {
  const fragment = document.createDocumentFragment()
  for (const line of text.lines) {
    const [dx, dy] = line.d
    // Dirección de escritura redondeada a uno de los cuatro ejes.
    const axis = Math.abs(dx) >= Math.abs(dy) ? (dx >= 0 ? 0 : 180) : dy < 0 ? -90 : 90
    const words = line.w
    for (let i = 0; i < words.length; i++) {
      const [x0, y0, x1, y1, word] = words[i]
      const next = words[i + 1]
      const span = document.createElement('span')
      // textContent: el texto del PDF nunca se interpreta como HTML (OWASP A05:2025).
      span.textContent = next ? `${word} ` : word
      let left = x0
      let top = y0
      let thickness = y1 - y0
      let length = (next ? Math.max(next[0], x1) : x1) - x0
      if (axis === 180) {
        left = x1
        top = y1
        length = x1 - (next ? Math.min(next[2], x0) : x0)
      } else if (axis === -90) {
        top = y1
        thickness = x1 - x0
        length = y1 - (next ? Math.min(next[3], y0) : y0)
      } else if (axis === 90) {
        left = x1
        thickness = x1 - x0
        length = (next ? Math.max(next[1], y1) : y1) - y0
      }
      if (thickness <= 0 || length <= 0) continue
      const size = line.s > 0 ? Math.min(line.s, thickness) : thickness
      const natural = naturalWidth(span.textContent, size)
      const stretch = natural > 0 ? length / natural : 1
      const style = span.style
      style.left = `${left}px`
      style.top = `${top}px`
      style.height = `${thickness}px`
      style.lineHeight = `${thickness}px`
      style.fontSize = `${size}px`
      style.transform = axis === 0 ? `scaleX(${stretch})` : `rotate(${axis}deg) scaleX(${stretch})`
      fragment.append(span)
    }
    // Salto de línea para que al copiar varias líneas no salgan pegadas.
    fragment.append(document.createElement('br'))
  }
  return fragment
}

export const TextLayer = memo(function TextLayer({ doc, page }: { doc: string; page: PageInfo }) {
  const text = usePageData<PageText>(doc, page.id, page.gen, 'text')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const host = ref.current
    if (!host) return
    host.replaceChildren(text ? buildLayer(text) : document.createDocumentFragment())
  }, [text])

  return <div ref={ref} className="text-layer" data-page={page.id} />
})
