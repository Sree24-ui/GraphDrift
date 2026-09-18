import { AnimatePresence, m } from 'motion/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { useMotionTransition } from '../hooks/useMotionTransition'
import { ToastContext, type ToastTone as Tone } from '../hooks/useToast'
import MaterialIcon from './MaterialIcon'

interface Toast {
  id: number
  message: string
  tone: Tone
}

// Errors stay up longer: they usually need reading, not just noticing.
const DISMISS_MS: Record<Tone, number> = { success: 4000, error: 9000 }

export default function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timers = useRef<number[]>([])
  const transition = useMotionTransition()

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach(window.clearTimeout)
  }, [])

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (message: string, tone: Tone = 'success') => {
      const id = Date.now() + Math.random()
      setToasts((prev) => [...prev.slice(-3), { id, message, tone }])
      timers.current.push(
        window.setTimeout(() => dismiss(id), DISMISS_MS[tone]),
      )
    },
    [dismiss],
  )

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <m.div
            key={toast.id}
            initial={{ opacity: 0, x: 24, scale: 0.97 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, scale: 0.97 }}
            transition={transition}
            role={toast.tone === 'error' ? 'alert' : 'status'}
            className={[
              'glass-panel pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-lg',
              toast.tone === 'error'
                ? 'border-error/40 bg-error/10 text-error'
                : 'border-primary/30 bg-primary/10 text-primary',
            ].join(' ')}
          >
            <MaterialIcon
              name={toast.tone === 'error' ? 'error' : 'check_circle'}
              size={16}
            />
            <span className="flex-1 pt-0.5">{toast.message}</span>
            <button
              type="button"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
              className="rounded p-0.5 opacity-70 hover:opacity-100"
            >
              <MaterialIcon name="close" size={14} />
            </button>
          </m.div>
        ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}
