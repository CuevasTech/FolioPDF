import { Eye, Highlighter, LayoutGrid, ListChecks, PencilLine, Settings, Signature, Sparkles, Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import { L } from '@/i18n'
import { cn } from '@/lib/util'
import { setMode } from '@/state/actions'
import { type DocView, type Mode, useApp } from '@/state/app'

const MODES: { mode: Mode; label: string; icon: ReactNode; hint: string }[] = [
  { mode: 'view', label: L.shell.rail.view.label, icon: <Eye size={19} />, hint: L.shell.rail.view.hint },
  { mode: 'edit', label: L.shell.rail.edit.label, icon: <PencilLine size={19} />, hint: L.shell.rail.edit.hint },
  { mode: 'annotate', label: L.shell.rail.annotate.label, icon: <Highlighter size={19} />, hint: L.shell.rail.annotate.hint },
  { mode: 'form', label: L.shell.rail.form.label, icon: <ListChecks size={19} />, hint: L.shell.rail.form.hint },
  { mode: 'sign', label: L.shell.rail.sign.label, icon: <Signature size={19} />, hint: L.shell.rail.sign.hint },
  { mode: 'pages', label: L.shell.rail.pages.label, icon: <LayoutGrid size={19} />, hint: L.shell.rail.pages.hint },
  { mode: 'tools', label: L.shell.rail.tools.label, icon: <Wrench size={19} />, hint: L.shell.rail.tools.hint }
]

// La IA solo aparece si el usuario la ha activado en Ajustes.
const AI_MODE = { mode: 'ai' as Mode, label: L.ai.rail.label, icon: <Sparkles size={19} />, hint: L.ai.rail.hint }

export function Rail({ doc }: { doc: DocView }) {
  const openDialog = useApp((s) => s.openDialog)
  const aiOn = useApp((s) => s.settings.aiTier !== 'off')
  const modes = aiOn ? [...MODES, AI_MODE] : MODES
  return (
    <nav className="rail" aria-label={L.shell.rail.label}>
      {modes.map((entry) => (
        <button
          key={entry.mode}
          className={cn('rail-item', doc.mode === entry.mode && 'on')}
          data-tip={entry.hint}
          data-tip-side="right"
          onClick={() => setMode(doc.id, entry.mode)}
        >
          {entry.icon}
          <span>{entry.label}</span>
        </button>
      ))}
      <div className="rail-spacer" />
      <button className="icon-btn" data-tip={L.shell.rail.settings} data-tip-side="right" onClick={() => openDialog({ type: 'settings' })}>
        <Settings size={18} />
      </button>
    </nav>
  )
}
