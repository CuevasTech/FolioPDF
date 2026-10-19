import {
  Coffee,
  Ellipsis,
  FileText,
  FolderOpen,
  Images,
  Info,
  Keyboard,
  Merge,
  MessageSquare,
  Plus,
  Printer,
  Redo2,
  Save,
  ScanLine,
  Settings,
  ShieldAlert,
  SquareArrowOutUpRight,
  Undo2,
  X
} from 'lucide-react'
import { L, fmt } from '@/i18n'
import { cn } from '@/lib/util'
import {
  closeDoc,
  imagesToPdf,
  mergeFiles,
  newBlank,
  openWithDialog,
  redo,
  saveDoc,
  saveDocAs,
  undo
} from '@/state/actions'
import { useActiveDoc, useApp } from '@/state/app'
import { printDoc } from '@/state/print'
import { openScan } from './ScanDialog'
import { Logo, MenuButton, type MenuEntry } from './ui'

export function TitleBar() {
  const order = useApp((s) => s.order)
  const docs = useApp((s) => s.docs)
  const active = useActiveDoc()
  const setActive = useApp((s) => s.setActive)
  const openDialog = useApp((s) => s.openDialog)
  const isolated = useApp((s) => s.isolated)
  const toast = useApp((s) => s.toast)

  const createMenu: MenuEntry[] = [
    { label: L.shell.titleBar.create.open, icon: <FolderOpen size={16} />, kbd: 'Ctrl+O', onSelect: () => void openWithDialog() },
    'sep',
    { label: L.shell.titleBar.create.blank, icon: <FileText size={16} />, kbd: 'Ctrl+N', onSelect: () => void newBlank() },
    { label: L.shell.titleBar.create.merge, icon: <Merge size={16} />, onSelect: () => void mergeFiles() },
    { label: L.shell.titleBar.create.images, icon: <Images size={16} />, onSelect: () => void imagesToPdf() },
    { label: L.shell.titleBar.create.scan, icon: <ScanLine size={16} />, onSelect: openScan }
  ]

  const moreMenu = (): MenuEntry[] => {
    const entries: MenuEntry[] = []
    if (active) {
      entries.push(
        { label: L.common.saveAs, icon: <Save size={16} />, kbd: L.shell.titleBar.menu.saveAsKbd, onSelect: () => void saveDocAs(active.id) },
        { label: L.shell.titleBar.menu.print, icon: <Printer size={16} />, kbd: 'Ctrl+P', onSelect: () => void printDoc(active.id) },
        {
          label: L.shell.titleBar.menu.showInFolder,
          icon: <SquareArrowOutUpRight size={16} />,
          disabled: !active.state.path,
          onSelect: () => void window.folio.files.showInFolder(active.id)
        },
        { label: L.shell.titleBar.menu.properties, icon: <Info size={16} />, onSelect: () => openDialog({ type: 'properties', doc: active.id }) },
        'sep'
      )
    }
    entries.push(
      { label: L.shell.titleBar.menu.settings, icon: <Settings size={16} />, kbd: 'Ctrl+,', onSelect: () => openDialog({ type: 'settings' }) },
      { label: L.shell.titleBar.menu.shortcuts, icon: <Keyboard size={16} />, onSelect: () => openDialog({ type: 'shortcuts' }) },
      { label: L.shell.titleBar.menu.feedback, icon: <MessageSquare size={16} />, onSelect: () => openDialog({ type: 'custom', id: 'feedback' }) },
      { label: L.shell.titleBar.menu.sponsor, icon: <Coffee size={16} />, onSelect: () => void window.folio.about.open('sponsors') },
      { label: L.shell.titleBar.menu.about, icon: <Info size={16} />, onSelect: () => openDialog({ type: 'about' }) }
    )
    return entries
  }

  return (
    <header className="titlebar">
      <div className="brand">
        <Logo size={21} />
      </div>
      <div className="tabs">
        {order.map((id) => {
          const doc = docs[id]
          if (!doc) return null
          return (
            <div
              key={id}
              className={cn('tab', id === active?.id && 'on')}
              title={doc.state.path ?? doc.state.name}
              onPointerDown={(event) => {
                if (event.button === 1) {
                  event.preventDefault()
                  void closeDoc(id)
                } else if (event.button === 0) setActive(id)
              }}
            >
              <span className="tab-name">{doc.state.name}</span>
              {doc.state.dirty && <span className="tab-dot" title={L.shell.titleBar.unsaved} />}
              <button
                className="tab-close"
                aria-label={fmt(L.shell.titleBar.closeTab, { name: doc.state.name })}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => void closeDoc(id)}
              >
                <X size={13} />
              </button>
            </div>
          )
        })}
      </div>
      <MenuButton className="icon-btn sm" entries={createMenu} tip={L.shell.titleBar.openOrCreate}>
        <Plus size={16} />
      </MenuButton>
      <div className="titlebar-fill" />
      <div className="titlebar-actions">
        {isolated === false && (
          <button
            className="icon-btn isolation-warning"
            data-tip={L.shell.titleBar.isolation.tip}
            aria-label={L.shell.titleBar.isolation.label}
            onClick={() =>
              toast('warning', L.shell.titleBar.isolation.warning)
            }
          >
            <ShieldAlert size={16} />
          </button>
        )}
        {active && (
          <>
            <button
              className="icon-btn"
              data-tip={active.state.undoLabel ? fmt(L.shell.titleBar.undoAction, { action: active.state.undoLabel }) : L.common.undo}
              disabled={!active.state.canUndo}
              onClick={() => void undo(active.id)}
            >
              <Undo2 size={16} />
            </button>
            <button
              className="icon-btn"
              data-tip={active.state.redoLabel ? fmt(L.shell.titleBar.redoAction, { action: active.state.redoLabel }) : L.common.redo}
              disabled={!active.state.canRedo}
              onClick={() => void redo(active.id)}
            >
              <Redo2 size={16} />
            </button>
            <button
              className={cn('icon-btn', active.state.dirty && 'on')}
              data-tip={active.state.dirty ? L.shell.titleBar.save : L.shell.titleBar.noChanges}
              onClick={() => void saveDoc(active.id)}
            >
              <Save size={16} />
            </button>
          </>
        )}
        <MenuButton className="icon-btn" entries={moreMenu} align="end" tip={L.shell.titleBar.more}>
          <Ellipsis size={17} />
        </MenuButton>
      </div>
    </header>
  )
}
