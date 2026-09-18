import { useReducedMotion } from 'motion/react'
import type { Transition } from 'motion/react'

/**
 * One transition for every animation in the app, zeroed when the OS asks for
 * reduced motion.
 *
 * `MotionConfig reducedMotion="user"` alone is not enough: it drops transform
 * and layout animations but deliberately keeps opacity and colour ones. A
 * duration of 0 means the element is simply in its final state.
 */
export function useMotionTransition(duration = 0.22): Transition {
  const reduced = useReducedMotion()
  return reduced ? { duration: 0 } : { duration, ease: 'easeOut' }
}
