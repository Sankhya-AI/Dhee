export const NEURAL = {
  // Backgrounds (dark depth layers)
  void: '#050510',
  cortex: '#0a0a1a',
  synapse: '#12122a',
  membrane: '#1a1a3a',

  // Memory types
  episodic: '#7c3aed',
  semantic: '#06d6a0',

  // Layers
  sml: '#22d3ee',
  lml: '#fbbf24',

  // Multi-trace
  sFast: '#ef4444',
  sMid: '#f59e0b',
  sSlow: '#22c55e',

  // Echo depths
  shallow: '#94a3b8',
  medium: '#a78bfa',
  deep: '#c084fc',

  // States
  conflict: '#ef4444',
  pending: '#f59e0b',
  success: '#22c55e',
  forgotten: '#334155',

  // Glow
  neuralGlow: '#7c3aed40',
  synapseGlow: '#22d3ee30',
} as const;

export const GLASS = {
  background: 'rgba(26, 26, 58, 0.6)',
  backdropFilter: 'blur(12px)',
  border: 'rgba(124, 58, 237, 0.15)',
  borderRadius: '12px',
} as const;

export function memoryTypeColor(type: string): string {
  return type === 'semantic' ? NEURAL.semantic : NEURAL.episodic;
}

export function layerColor(layer: string): string {
  return layer === 'lml' ? NEURAL.lml : NEURAL.sml;
}

export function traceColor(trace: 'fast' | 'mid' | 'slow'): string {
  switch (trace) {
    case 'fast': return NEURAL.sFast;
    case 'mid': return NEURAL.sMid;
    case 'slow': return NEURAL.sSlow;
  }
}

export function echoDepthColor(depth: string): string {
  switch (depth) {
    case 'deep': return NEURAL.deep;
    case 'medium': return NEURAL.medium;
    default: return NEURAL.shallow;
  }
}

export function strengthToGlow(strength: number): number {
  return 0.3 + strength * 0.7;
}

export function strengthToSize(strength: number, min = 4, max = 16): number {
  return min + strength * (max - min);
}
