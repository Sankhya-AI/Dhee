// Subtree-width-packed tidy tree layout. Pure function, no side effects.
//
// Each node has a fixed (w, h). For each node we compute the maximum of
// (own width, sum of children's subtree widths + gap*(n-1)). Then x is
// assigned by left-packing children inside that subtree. y is depth * levelH.
// Children are centred under their parent unless that would force overlap.

export interface TreeNodeIn {
  id: string;
  parent: string | null;
  width: number;
  height: number;
  depth: number;
}

export interface TreeNodeOut extends TreeNodeIn {
  x: number;
  y: number;
}

export interface TreeLayoutResult {
  nodes: TreeNodeOut[];
  bounds: { minX: number; minY: number; maxX: number; maxY: number };
}

interface Internal {
  id: string;
  parent: string | null;
  children: string[];
  width: number;
  height: number;
  depth: number;
  subtreeWidth: number;
  x: number;
  y: number;
}

export function layoutTree(
  inputs: TreeNodeIn[],
  options: {
    siblingGap?: number;
    levelGap?: number;
  } = {}
): TreeLayoutResult {
  const SIBLING_GAP = options.siblingGap ?? 24;
  const LEVEL_GAP = options.levelGap ?? 100;
  const map = new Map<string, Internal>();
  for (const n of inputs) {
    map.set(n.id, {
      id: n.id,
      parent: n.parent,
      children: [],
      width: n.width,
      height: n.height,
      depth: n.depth,
      subtreeWidth: n.width,
      x: 0,
      y: 0,
    });
  }
  const roots: Internal[] = [];
  for (const n of map.values()) {
    if (n.parent && map.has(n.parent)) {
      map.get(n.parent)!.children.push(n.id);
    } else {
      roots.push(n);
    }
  }

  const computeSubtree = (id: string): number => {
    const n = map.get(id)!;
    if (!n.children.length) {
      n.subtreeWidth = n.width;
      return n.subtreeWidth;
    }
    let total = 0;
    for (const cid of n.children) total += computeSubtree(cid);
    total += SIBLING_GAP * (n.children.length - 1);
    n.subtreeWidth = Math.max(n.width, total);
    return n.subtreeWidth;
  };

  // Compute heights per level (max h within depth) so y positions are even.
  const levelHeight = new Map<number, number>();
  for (const n of map.values()) {
    levelHeight.set(n.depth, Math.max(levelHeight.get(n.depth) || 0, n.height));
  }
  const cumY = new Map<number, number>();
  let runningY = 0;
  const maxDepth = Math.max(0, ...Array.from(levelHeight.keys()));
  for (let d = 0; d <= maxDepth; d += 1) {
    cumY.set(d, runningY);
    runningY += (levelHeight.get(d) || 0) + LEVEL_GAP;
  }

  const positionSubtree = (id: string, leftX: number): void => {
    const n = map.get(id)!;
    n.y = cumY.get(n.depth) || 0;
    if (!n.children.length) {
      n.x = leftX + n.width / 2;
      return;
    }
    let cursor = leftX;
    let childCenters: number[] = [];
    for (const cid of n.children) {
      const child = map.get(cid)!;
      positionSubtree(cid, cursor);
      childCenters.push(child.x);
      cursor += child.subtreeWidth + SIBLING_GAP;
    }
    if (childCenters.length === 0) {
      n.x = leftX + n.width / 2;
    } else {
      const minC = Math.min(...childCenters);
      const maxC = Math.max(...childCenters);
      n.x = (minC + maxC) / 2;
    }
  };

  let cursor = 0;
  for (const root of roots) {
    computeSubtree(root.id);
    positionSubtree(root.id, cursor);
    cursor += root.subtreeWidth + SIBLING_GAP * 2;
  }

  // Compute final bounds
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const out: TreeNodeOut[] = [];
  for (const n of map.values()) {
    const halfW = n.width / 2;
    minX = Math.min(minX, n.x - halfW);
    maxX = Math.max(maxX, n.x + halfW);
    minY = Math.min(minY, n.y);
    maxY = Math.max(maxY, n.y + n.height);
    out.push({
      id: n.id,
      parent: n.parent,
      width: n.width,
      height: n.height,
      depth: n.depth,
      x: n.x,
      y: n.y,
    });
  }
  if (!Number.isFinite(minX)) {
    minX = 0;
    minY = 0;
    maxX = 0;
    maxY = 0;
  }
  return {
    nodes: out,
    bounds: { minX, minY, maxX, maxY },
  };
}
