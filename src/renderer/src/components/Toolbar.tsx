import { Hand, Search, TextCursor } from 'lucide-react'
import type { ReactNode } from 'react'
import { L } from '@/i18n'
import { cn } from '@/lib/util'
import { setTool } from '@/state/actions'
import type { DocView, Mode } from '@/state/app'
import { openSearch } from '@/state/search'

/** Botón de herramienta de la barra flotante. */
export function ToolButton({
  doc,
  tool,
  icon,
  label,
  tip,
  compact
}: {
  doc: DocView
  tool: string
  icon: ReactNode
  label: string
  tip?: string
  compact?: boolean
}) {
  return (
    <button
      className={cn('tool', compact && 'icon', doc.tool === tool && 'on')}
      data-tip={tip ?? (compact ? label : undefined)}
      aria-pressed={doc.tool === tool}
      // Volver a pulsar la herramienta activa la suelta (vuelve al puntero).
      onClick={() => setTool(doc.id, doc.tool === tool && tool !== '' ? '' : tool)}
    >
      {icon}
      {!compact && <span>{label}</span>}
    </button>
  )
}

export function ActionButton({
  icon,
  label,
  tip,
  onClick,
  disabled,
  compact,
  active
}: {
  icon: ReactNode
  label: string
  tip?: string
  onClick: () => void
  disabled?: boolean
  compact?: boolean
  active?: boolean
}) {
  return (
    <button
      className={cn('tool', compact && 'icon', active && 'on')}
      data-tip={tip ?? (compact ? label : undefined)}
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
      {!compact && <span>{label}</span>}
    </button>
  )
}

function ViewTools({ doc }: { doc: DocView }) {
  return (
    <>
      <ToolButton doc={doc} tool="" icon={<TextCursor size={16} />} label={L.shell.toolbar.select} tip={L.shell.toolbar.selectTip} />
      <ToolButton doc={doc} tool="hand" icon={<Hand size={16} />} label={L.shell.toolbar.hand} tip={L.shell.toolbar.handTip} />
      <div className="sep" />
      <ActionButton icon={<Search size={16} />} label={L.shell.toolbar.search} tip={L.shell.toolbar.searchTip} onClick={() => openSearch(doc.id)} active={doc.search.open} />
    </>
  )
}

/** Barras de cada modo; los módulos de funciones registran la suya. */
const toolbars = new Map<Mode, (props: { doc: DocView }) => ReactNode>([['view', ViewTools]])

export function registerToolbar(mode: Mode, component: (props: { doc: DocView }) => ReactNode): void {
  toolbars.set(mode, component)
}

export function Toolbar({ doc }: { doc: DocView }) {
  const Tools = toolbars.get(doc.mode)
  if (!Tools) return null
  return (
    <div className="toolbar" role="toolbar" aria-label={L.shell.toolbar.label}>
      <Tools doc={doc} />
    </div>
  )
}
