interface TableSkeletonProps {
  rows?: number
  columns?: number
}

export default function TableSkeleton({
  rows = 6,
  columns = 6,
}: TableSkeletonProps) {
  return (
    <div className="animate-pulse px-4 py-3" aria-hidden="true">
      <div className="mb-4 flex gap-3">
        {Array.from({ length: columns }).map((_, i) => (
          <div
            key={`head-${i}`}
            className="h-3 flex-1 rounded bg-surface-bright"
          />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, row) => (
        <div key={`row-${row}`} className="mb-3 flex gap-3">
          {Array.from({ length: columns }).map((_, col) => (
            <div
              key={`cell-${row}-${col}`}
              className="h-8 flex-1 rounded bg-surface-bright/70"
            />
          ))}
        </div>
      ))}
    </div>
  )
}
