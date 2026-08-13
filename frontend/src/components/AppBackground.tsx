import { useMemo } from 'react'

interface BubbleSpec {
  size: number
  left: number
  duration: number
  delay: number
}

function randomBubbles(count: number): BubbleSpec[] {
  return Array.from({ length: count }, () => ({
    size: Math.random() * 80 + 40,
    left: Math.random() * 100,
    duration: Math.random() * 15 + 18,
    delay: Math.random() * 10,
  }))
}

export default function AppBackground() {
  const bubbles = useMemo(() => randomBubbles(12), [])

  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute left-[-10%] top-[-20%] h-[50%] w-[50%] rounded-full bg-primary/5 blur-[120px]" />
      <div className="absolute bottom-[-20%] right-[-10%] h-[60%] w-[60%] rounded-full bg-tertiary/5 blur-[150px]" />
      {bubbles.map((bubble, index) => (
        <div
          key={index}
          className="bubble"
          style={{
            width: bubble.size,
            height: bubble.size,
            left: `${bubble.left}%`,
            bottom: '-80px',
            animationDuration: `${bubble.duration}s`,
            animationDelay: `${bubble.delay}s`,
          }}
        />
      ))}
    </div>
  )
}
