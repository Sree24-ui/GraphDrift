import type { GraphNode, GraphSnapshot } from '../api/types'

/** Node is visually "flagged" in replay when fusion produced a confidence label. */
export function isReplayFlagged(node: GraphNode): boolean {
  return node.confidence !== null
}

export function flaggedAccountIds(snapshot: GraphSnapshot): Set<string> {
  return new Set(
    snapshot.nodes.filter(isReplayFlagged).map((node) => node.account_id),
  )
}

export function findNewlyFlagged(
  previous: Set<string>,
  current: Set<string>,
): string[] {
  return [...current].filter((id) => !previous.has(id))
}
