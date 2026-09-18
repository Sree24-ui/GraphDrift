import { useEffect, useState } from 'react'
import type { RefObject } from 'react'

export type QueueShortcutAction = 'confirmed' | 'false_positive'

interface Options {
  containerRef: RefObject<HTMLElement | null>
  /** Runs the analyst's decision for a row. Resolve when the list has updated. */
  onAction: (rowId: string, action: QueueShortcutAction) => void | Promise<void>
  enabled?: boolean
}

// Rows opt in with data-queue-row, data-queue-id and data-queue-unreviewed.
const ROW = '[data-queue-row]'
const TYPING = 'input, textarea, select, [contenteditable="true"]'

/**
 * Triage keys for a queue table: j/k (or arrows) to move, c to confirm,
 * x for false positive, ? for the legend. Enter/Space stay with the row itself.
 * Nothing is bound while the user is typing or holding a modifier, so existing
 * mouse and tab behaviour is untouched.
 */
export function useQueueShortcuts({ containerRef, onAction, enabled = true }: Options) {
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    if (!enabled) {
      return
    }
    const rows = () =>
      Array.from(containerRef.current?.querySelectorAll<HTMLElement>(ROW) ?? [])
    const focusRow = (row: HTMLElement | undefined) => {
      row?.focus()
      row?.scrollIntoView({ block: 'nearest' })
    }

    const handler = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if ((event.target as HTMLElement | null)?.closest?.(TYPING)) return

      if (event.key === 'Escape') {
        setHelpOpen(false)
        return
      }
      if (event.key === '?') {
        event.preventDefault()
        setHelpOpen((open) => !open)
        return
      }

      const list = rows()
      if (list.length === 0) return
      const current = (document.activeElement as HTMLElement | null)?.closest?.(
        ROW,
      ) as HTMLElement | null
      const index = current ? list.indexOf(current) : -1

      if (event.key === 'j' || event.key === 'ArrowDown') {
        event.preventDefault()
        focusRow(list[Math.min(index + 1, list.length - 1)])
        return
      }
      if (event.key === 'k' || event.key === 'ArrowUp') {
        event.preventDefault()
        focusRow(list[index <= 0 ? 0 : index - 1])
        return
      }
      if ((event.key === 'c' || event.key === 'x') && current?.dataset.queueId) {
        event.preventDefault()
        const rowId = current.dataset.queueId
        void Promise.resolve(
          onAction(rowId, event.key === 'c' ? 'confirmed' : 'false_positive'),
        ).then(() => {
          // Once the list has re-rendered, land on the next row still awaiting
          // a decision — the actual next step in the triage loop.
          requestAnimationFrame(() => {
            const updated = rows()
            const next =
              updated
                .slice(Math.max(index, 0))
                .find((row) => row.dataset.queueUnreviewed === 'true') ??
              updated[Math.min(Math.max(index, 0), updated.length - 1)]
            focusRow(next)
          })
        })
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [containerRef, onAction, enabled])

  return { helpOpen, setHelpOpen }
}
