import type { ReactNode } from 'react'
import type { DocView, Mode } from '@/state/app'
import { Viewer } from '@/viewer/Viewer'
import { Dock } from './Dock'
import { FindBar } from './FindBar'
import { Rail } from './Rail'
import { SidePanel } from './SidePanel'
import { Toolbar } from './Toolbar'

type Part = (props: { doc: DocView }) => ReactNode

/** Piezas que aporta cada modo: vista que sustituye al visor, panel derecho y extras sobre el lienzo. */
const stages = new Map<Mode, { component: Part; when?: (doc: DocView) => boolean }>()
const inspectors: Part[] = []
const overlays: Part[] = []

/** `when` permite que un modo use el visor normal en algunas de sus herramientas. */
export function registerStage(mode: Mode, component: Part, when?: (doc: DocView) => boolean): void {
  stages.set(mode, { component, when })
}
export function registerInspector(component: Part): void {
  inspectors.push(component)
}
export function registerOverlay(component: Part): void {
  overlays.push(component)
}

export function Workspace({ doc }: { doc: DocView }) {
  const entry = stages.get(doc.mode)
  const Stage = entry && (!entry.when || entry.when(doc)) ? entry.component : undefined
  return (
    <div className="body">
      <Rail doc={doc} />
      {doc.side && !Stage && <SidePanel doc={doc} />}
      <main className="stage">
        {Stage ? (
          <Stage doc={doc} />
        ) : (
          <>
            <Viewer doc={doc} />
            {doc.search.open ? <FindBar doc={doc} /> : <Toolbar doc={doc} />}
            {overlays.map((Overlay, index) => (
              <Overlay key={index} doc={doc} />
            ))}
            <Dock doc={doc} />
          </>
        )}
      </main>
      {inspectors.map((Inspector, index) => (
        <Inspector key={index} doc={doc} />
      ))}
    </div>
  )
}
