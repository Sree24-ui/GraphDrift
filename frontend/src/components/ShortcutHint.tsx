import { useEffect } from 'react'
import { createPortal } from 'react-dom'

import MaterialIcon from './MaterialIcon'

const STORAGE_KEY = 'graphdrift.queueShortcutsSeen'

const KEYS: [string, string][] = [
  ['j / ↓', 'Next case'],
  ['k / ↑', 'Previous case'],
  ['Enter', 'Open or close details'],
  ['c', 'Confirm fraud'],
  ['x', 'Mark false positive'],
  ['?', 'Show or hide this list'],
]

interface ShortcutHintProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  label: string
}

export default function ShortcutHint({ open, onOpenChange, label }: ShortcutHintProps) {
  useEffect(() => {
    // Show once, unprompted, then leave it to the ? key.
    try {
      if (!localStorage.getItem(STORAGE_KEY)) {
        localStorage.setItem(STORAGE_KEY, '1')
        onOpenChange(true)
      }
    } catch {
      // Private mode or blocked storage: just don't auto-open.
    }
  }, [onOpenChange])

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onOpenChange(true)}
        aria-keyshortcuts="?"
        className="flex items-center gap-1 text-[10px] text-on-surface-variant hover:text-on-surface"
      >
        <MaterialIcon name="keyboard" size={14} />
        Shortcuts (?)
      </button>
    )
  }

  // Portalled: the glass panels use backdrop-filter, which makes an ancestor
  // the containing block for position:fixed and would strand this mid-header.
  return createPortal(
    <div
      role="dialog"
      aria-label={`${label} keyboard shortcuts`}
      className="glass-panel fixed bottom-4 left-4 z-[90] w-60 rounded-xl border border-primary/20 p-3 text-xs shadow-lg md:left-[17rem]"
    >
      <div className="mb-2 flex items-center justify-between">
        <p className="font-semibold text-on-surface">Keyboard shortcuts</p>
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          aria-label="Close keyboard shortcuts"
          className="rounded p-0.5 text-on-surface-variant hover:text-on-surface"
        >
          <MaterialIcon name="close" size={14} />
        </button>
      </div>
      <dl className="space-y-1">
        {KEYS.map(([key, description]) => (
          <div key={key} className="flex items-center justify-between gap-3">
            <dt className="shrink-0">
              <kbd className="rounded border border-primary/20 bg-surface/60 px-1.5 py-0.5 font-mono text-[10px] text-primary">
                {key}
              </kbd>
            </dt>
            <dd className="text-right text-[11px] text-on-surface-variant">
              {description}
            </dd>
          </div>
        ))}
      </dl>
    </div>,
    document.body,
  )
}
