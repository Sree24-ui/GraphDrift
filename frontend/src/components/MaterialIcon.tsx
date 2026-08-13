interface MaterialIconProps {
  name: string
  className?: string
  filled?: boolean
  size?: number
}

export default function MaterialIcon({
  name,
  className = '',
  filled = false,
  size = 20,
}: MaterialIconProps) {
  return (
    <span
      className={`material-symbols-outlined leading-none ${className}`}
      style={{
        fontSize: size,
        fontVariationSettings: filled ? "'FILL' 1" : "'FILL' 0",
      }}
      aria-hidden
    >
      {name}
    </span>
  )
}
