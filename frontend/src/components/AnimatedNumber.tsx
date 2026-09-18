import { animate, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'

interface AnimatedNumberProps {
  value: number | null
  /** Shown when there is no value yet, so an empty feed never reads as zero. */
  placeholder?: string
  className?: string
}

/**
 * Counts from the previous value to the new one when the live feed updates,
 * and flashes the accent colour on arrival — the metric cards otherwise jumped
 * silently, which is most of why a live dashboard looked static.
 *
 * Reduced motion: no count-up and no flash, the number simply changes.
 */
export default function AnimatedNumber({
  value,
  placeholder = '—',
  className = '',
}: AnimatedNumberProps) {
  const reduced = useReducedMotion()
  const [shown, setShown] = useState(value)
  const previous = useRef(value)
  const [flash, setFlash] = useState(false)

  useEffect(() => {
    if (value === null) {
      previous.current = null
      setShown(null)
      return
    }
    const from = previous.current
    previous.current = value
    if (reduced || from === null || from === value) {
      setShown(value)
      return
    }
    setFlash(true)
    const flashTimer = window.setTimeout(() => setFlash(false), 700)
    const controls = animate(from, value, {
      duration: Math.min(0.8, 0.25 + Math.abs(value - from) / 400),
      ease: 'easeOut',
      onUpdate: (latest) => setShown(Math.round(latest)),
    })
    return () => {
      controls.stop()
      window.clearTimeout(flashTimer)
    }
  }, [value, reduced])

  return (
    <span
      className={className}
      style={
        flash && !reduced
          ? { animation: 'valueFlash 0.7s ease-out forwards' }
          : undefined
      }
    >
      {shown === null ? placeholder : shown.toLocaleString()}
    </span>
  )
}
