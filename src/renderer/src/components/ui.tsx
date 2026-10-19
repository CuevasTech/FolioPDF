import { Check } from 'lucide-react'
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/util'

/** Marca de la aplicación: una hoja con la esquina doblada. */
export function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M6.5 2.75h7.4a2 2 0 0 1 1.42.59l3.34 3.34a2 2 0 0 1 .59 1.42v10.65a2.5 2.5 0 0 1-2.5 2.5H6.5a2.5 2.5 0 0 1-2.5-2.5V5.25a2.5 2.5 0 0 1 2.5-2.5Z"
        fill="currentColor"
        fillOpacity="0.16"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M14 3v3.5a1.5 1.5 0 0 0 1.5 1.5H19" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M8 12.5h8M8 16h5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

// ------------------------------------------------------------------- menús

export type MenuEntry =
  | {
      label: string
      icon?: ReactNode
      kbd?: string
      checked?: boolean
      disabled?: boolean
      danger?: boolean
      onSelect: () => void
    }
  | { heading: string }
  | 'sep'

interface MenuProps {
  /** Rectángulo del elemento que abre el menú, o un punto de la pantalla. */
  anchor: DOMRect | { x: number; y: number }
  align?: 'start' | 'end'
  side?: 'bottom' | 'top'
  entries: MenuEntry[]
  onClose: () => void
  minWidth?: number
}

export function Menu({ anchor, align = 'start', side = 'bottom', entries, onClose, minWidth }: MenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const box = element.getBoundingClientRect()
    const rect = 'width' in anchor ? anchor : new DOMRect(anchor.x, anchor.y, 0, 0)
    let left = align === 'end' ? rect.right - box.width : rect.left
    let top = side === 'top' ? rect.top - box.height - 6 : rect.bottom + 6
    // Siempre dentro de la ventana.
    left = Math.max(8, Math.min(left, window.innerWidth - box.width - 8))
    if (top + box.height > window.innerHeight - 8) top = Math.max(8, rect.top - box.height - 6)
    if (top < 8) top = 8
    setPosition({ left, top })
  }, [anchor, align, side])

  useEffect(() => {
    const onDown = (event: PointerEvent): void => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose()
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('pointerdown', onDown, true)
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('blur', onClose)
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('blur', onClose)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  return createPortal(
    <div
      ref={ref}
      className="menu"
      role="menu"
      style={{ left: position?.left ?? 0, top: position?.top ?? 0, visibility: position ? 'visible' : 'hidden', minWidth }}
    >
      {entries.map((entry, index) => {
        if (entry === 'sep') return <div key={index} className="menu-sep" />
        if ('heading' in entry) {
          return (
            <div key={index} className="menu-label">
              {entry.heading}
            </div>
          )
        }
        return (
          <button
            key={index}
            role="menuitem"
            className={cn('menu-item', entry.danger && 'danger')}
            disabled={entry.disabled}
            onClick={() => {
              onClose()
              entry.onSelect()
            }}
          >
            {entry.icon}
            <span>{entry.label}</span>
            {entry.checked ? <Check size={15} className="tick" /> : entry.kbd ? <span className="kbd">{entry.kbd}</span> : null}
          </button>
        )
      })}
    </div>,
    document.body
  )
}

/** Botón que abre un menú anclado a sí mismo. */
export function MenuButton({
  className,
  entries,
  children,
  align,
  side,
  tip,
  tipSide,
  disabled
}: {
  className?: string
  entries: MenuEntry[] | (() => MenuEntry[])
  children: ReactNode
  align?: 'start' | 'end'
  side?: 'bottom' | 'top'
  tip?: string
  tipSide?: 'top' | 'right' | 'left'
  disabled?: boolean
}) {
  const ref = useRef<HTMLButtonElement>(null)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  return (
    <>
      <button
        ref={ref}
        className={cn(className, anchor && 'on')}
        data-tip={anchor ? undefined : tip}
        data-tip-side={tipSide}
        disabled={disabled}
        onClick={() => setAnchor(ref.current?.getBoundingClientRect() ?? null)}
      >
        {children}
      </button>
      {anchor && (
        <Menu
          anchor={anchor}
          align={align}
          side={side}
          entries={typeof entries === 'function' ? entries() : entries}
          onClose={() => setAnchor(null)}
        />
      )}
    </>
  )
}

// ------------------------------------------------------------------ diálogo

export function Dialog({
  title,
  children,
  footer,
  onClose,
  size,
  icon,
  className
}: {
  title: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  size?: 'wide' | 'xl'
  icon?: ReactNode
  /** Clase propia para diálogos con un diseño particular (p. ej. imprimir). */
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // El foco entra en el diálogo al abrirse: lo marcado con data-autofocus, el primer campo o el botón principal.
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const target =
      root.querySelector<HTMLElement>('[data-autofocus]') ??
      root.querySelector<HTMLElement>('input:not([type=checkbox]):not([type=radio]), textarea, select') ??
      root.querySelector<HTMLElement>('.btn.primary, .btn.danger')
    target?.focus()
  }, [])

  return createPortal(
    <div
      className="backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div ref={ref} className={cn('dialog', size, className)} role="dialog" aria-modal="true" aria-label={title}>
        <div className="dialog-head">
          {icon}
          <h2>{title}</h2>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body
  )
}

// -------------------------------------------------------------- utilidades

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  )
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  full
}: {
  value: T
  options: { value: T; label: ReactNode; tip?: string }[]
  onChange: (value: T) => void
  full?: boolean
}) {
  return (
    <div className={cn('segmented', full && 'full')}>
      {options.map((option) => (
        <button
          key={option.value}
          className={cn(option.value === value && 'on')}
          data-tip={option.tip}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Checkbox({
  checked,
  onChange,
  children,
  disabled
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  children: ReactNode
  disabled?: boolean
}) {
  return (
    <label className="check" style={disabled ? { opacity: 0.5 } : undefined}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span>{children}</span>
    </label>
  )
}
