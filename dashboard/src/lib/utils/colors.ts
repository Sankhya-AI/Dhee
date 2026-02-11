import { NEURAL } from './neural-palette';

export const COLORS = {
  sml: NEURAL.sml,
  lml: NEURAL.lml,
  brand: NEURAL.episodic,
  destructive: NEURAL.conflict,
  success: NEURAL.success,
  scene: '#6b7280',
  category: NEURAL.episodic,
  entity: NEURAL.semantic,
  episodic: NEURAL.episodic,
  semantic: NEURAL.semantic,
  sFast: NEURAL.sFast,
  sMid: NEURAL.sMid,
  sSlow: NEURAL.sSlow,
} as const;

export function layerColor(layer: string): string {
  return layer === 'lml' ? COLORS.lml : COLORS.sml;
}

export function profileTypeColor(type: string): string {
  switch (type) {
    case 'self': return COLORS.lml;
    case 'contact': return COLORS.sml;
    case 'entity': return COLORS.brand;
    default: return COLORS.scene;
  }
}
