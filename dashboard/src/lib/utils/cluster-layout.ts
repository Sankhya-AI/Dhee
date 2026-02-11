import type { Memory } from "@/lib/types/memory";
import type { ClusterDimension } from "@/lib/stores/cluster-store";

export interface ClusterGroup {
  id: string;
  label: string;
  memories: Memory[];
  color: string;
  x: number;
  y: number;
  radius: number;
}

export interface NodePosition {
  id: string;
  x: number;
  y: number;
  clusterIndex: number;
}

const CLUSTER_COLORS = [
  '#7c3aed', '#06d6a0', '#22d3ee', '#fbbf24', '#ef4444',
  '#f59e0b', '#22c55e', '#a78bfa', '#c084fc', '#60a5fa',
  '#f472b6', '#34d399', '#fb923c', '#818cf8', '#94a3b8',
];

function groupBy(memories: Memory[], dimension: ClusterDimension): Map<string, Memory[]> {
  const groups = new Map<string, Memory[]>();

  for (const m of memories) {
    let keys: string[];
    switch (dimension) {
      case 'category':
        keys = m.categories.length > 0 ? [m.categories[0]] : ['Uncategorized'];
        break;
      case 'memory_type':
        keys = [m.memory_type || 'episodic'];
        break;
      case 'layer':
        keys = [m.layer];
        break;
      case 'scene':
        keys = [m.scene_id || 'No Scene'];
        break;
      case 'echo_depth':
        keys = [m.metadata?.echo_depth || 'none'];
        break;
      case 'strength': {
        const s = m.strength;
        keys = [s < 0.3 ? 'Weak' : s < 0.7 ? 'Moderate' : 'Strong'];
        break;
      }
      case 'time': {
        const age = (Date.now() - new Date(m.created_at).getTime()) / (1000 * 60 * 60 * 24);
        keys = [age < 1 ? 'Today' : age < 7 ? 'This Week' : age < 30 ? 'This Month' : 'Older'];
        break;
      }
      case 'profile':
        keys = [m.user_id || 'Unknown'];
        break;
      default:
        keys = ['Other'];
    }

    for (const key of keys) {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(m);
    }
  }

  return groups;
}

export function computeClusterLayout(
  memories: Memory[],
  dimension: ClusterDimension,
  width: number,
  height: number,
): { clusters: ClusterGroup[]; nodes: NodePosition[] } {
  const groups = groupBy(memories, dimension);
  const entries = Array.from(groups.entries()).sort((a, b) => b[1].length - a[1].length);

  const clusters: ClusterGroup[] = [];
  const nodes: NodePosition[] = [];
  const totalGroups = entries.length;

  // Pack clusters in a circle layout
  const cx = width / 2;
  const cy = height / 2;
  const layoutRadius = Math.min(width, height) * 0.35;

  entries.forEach(([label, mems], i) => {
    const angle = (i / Math.max(1, totalGroups)) * Math.PI * 2 - Math.PI / 2;
    const clusterRadius = Math.max(40, Math.sqrt(mems.length) * 20);

    const x = totalGroups === 1 ? cx : cx + Math.cos(angle) * layoutRadius;
    const y = totalGroups === 1 ? cy : cy + Math.sin(angle) * layoutRadius;

    clusters.push({
      id: `cluster-${i}`,
      label,
      memories: mems,
      color: CLUSTER_COLORS[i % CLUSTER_COLORS.length],
      x,
      y,
      radius: clusterRadius,
    });

    // Place nodes within cluster
    mems.forEach((m, j) => {
      const nodeAngle = (j / mems.length) * Math.PI * 2;
      const nodeR = clusterRadius * 0.7 * Math.sqrt(Math.random());

      nodes.push({
        id: m.id,
        x: x + Math.cos(nodeAngle) * nodeR,
        y: y + Math.sin(nodeAngle) * nodeR,
        clusterIndex: i,
      });
    });
  });

  return { clusters, nodes };
}
