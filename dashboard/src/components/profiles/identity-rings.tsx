"use client";

import { useProfiles } from "@/lib/hooks/use-profiles";
import { NEURAL } from "@/lib/utils/neural-palette";
import type { Profile } from "@/lib/types/profile";

function ProfileHub({ profile, ring, angle, totalInRing }: {
  profile: Profile;
  ring: number;
  angle: number;
  totalInRing: number;
}) {
  const typeColors: Record<string, string> = {
    self: NEURAL.lml,
    contact: NEURAL.sml,
    entity: NEURAL.semantic,
  };
  const color = typeColors[profile.type] || NEURAL.episodic;

  // Position on ring
  const radius = ring === 0 ? 0 : ring * 120;
  const x = 50 + (radius * Math.cos(angle * Math.PI / 180)) / 3.5;
  const y = 50 + (radius * Math.sin(angle * Math.PI / 180)) / 3.5;

  const factCount = (profile.facts?.length || 0) + (profile.preferences?.length || 0);

  return (
    <div
      className="absolute transform -translate-x-1/2 -translate-y-1/2 group cursor-pointer"
      style={{ left: `${x}%`, top: `${y}%` }}
    >
      {/* Glow */}
      <div
        className="absolute inset-0 rounded-full animate-breathe"
        style={{
          width: ring === 0 ? 80 : 48,
          height: ring === 0 ? 80 : 48,
          transform: 'translate(-50%, -50%)',
          left: '50%',
          top: '50%',
          background: `radial-gradient(circle, ${color}20 0%, transparent 70%)`,
        }}
      />

      {/* Node */}
      <div
        className="relative rounded-full flex items-center justify-center transition-transform group-hover:scale-110"
        style={{
          width: ring === 0 ? 64 : 40,
          height: ring === 0 ? 64 : 40,
          border: `2px solid ${color}50`,
          backgroundColor: `${color}15`,
          boxShadow: `0 0 12px ${color}30`,
        }}
      >
        <span className="text-xs font-medium text-white truncate max-w-[50px] text-center leading-tight px-1">
          {profile.name.split(' ')[0]}
        </span>
      </div>

      {/* Tooltip */}
      <div
        className="absolute left-1/2 -translate-x-1/2 top-full mt-2 glass-subtle px-3 py-2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 whitespace-nowrap"
      >
        <p className="text-xs font-medium text-white">{profile.name}</p>
        <p className="text-[10px]" style={{ color: NEURAL.shallow }}>
          {profile.type} &middot; {factCount} facts
        </p>
      </div>
    </div>
  );
}

export function IdentityRings() {
  const { data } = useProfiles();
  const profiles = data?.profiles ?? [];

  // Group by type
  const self = profiles.filter(p => p.type === 'self');
  const contacts = profiles.filter(p => p.type === 'contact');
  const entities = profiles.filter(p => p.type === 'entity');

  return (
    <div className="relative w-full max-w-2xl mx-auto" style={{ aspectRatio: '1 / 1' }}>
      {/* Ring circles */}
      {[1, 2, 3].map(ring => (
        <div
          key={ring}
          className="absolute rounded-full"
          style={{
            width: `${ring * 33}%`,
            height: `${ring * 33}%`,
            left: `${50 - ring * 16.5}%`,
            top: `${50 - ring * 16.5}%`,
            border: `1px solid rgba(124, 58, 237, ${0.12 - ring * 0.03})`,
          }}
        />
      ))}

      {/* Ring labels */}
      <div className="absolute top-2 left-1/2 -translate-x-1/2 text-[9px] font-medium" style={{ color: NEURAL.forgotten }}>
        ENTITIES
      </div>
      <div className="absolute" style={{ top: '18%', left: '50%', transform: 'translateX(-50%)' }}>
        <span className="text-[9px] font-medium" style={{ color: NEURAL.forgotten }}>CONTACTS</span>
      </div>

      {/* Self (center) */}
      {self.map((p, i) => (
        <ProfileHub key={p.id} profile={p} ring={0} angle={0} totalInRing={1} />
      ))}

      {/* Contacts (middle ring) */}
      {contacts.map((p, i) => (
        <ProfileHub
          key={p.id}
          profile={p}
          ring={1}
          angle={(360 / Math.max(1, contacts.length)) * i - 90}
          totalInRing={contacts.length}
        />
      ))}

      {/* Entities (outer ring) */}
      {entities.map((p, i) => (
        <ProfileHub
          key={p.id}
          profile={p}
          ring={2}
          angle={(360 / Math.max(1, entities.length)) * i - 90}
          totalInRing={entities.length}
        />
      ))}
    </div>
  );
}
