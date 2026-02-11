"use client";

import { useProfiles } from "@/lib/hooks/use-profiles";
import { IdentityRings } from "@/components/profiles/identity-rings";
import { EmptyState } from "@/components/shared/empty-state";
import { Users } from "lucide-react";
import { NEURAL } from "@/lib/utils/neural-palette";

export default function ProfilesPage() {
  const { data, isLoading } = useProfiles();
  const profiles = data?.profiles ?? [];

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-white">Identity Cortex</h1>
        <p className="text-xs" style={{ color: '#64748b' }}>
          Concentric rings — self at center, contacts in middle, entities at outer edge
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <div className="animate-neural-pulse text-purple-400 text-sm">Mapping identities...</div>
        </div>
      ) : profiles.length === 0 ? (
        <EmptyState
          title="No profiles yet"
          description="Profiles are auto-detected from conversations."
          icon={Users}
        />
      ) : (
        <>
          <IdentityRings />

          {/* Profile list */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {profiles.map((profile) => {
              const typeColors: Record<string, string> = {
                self: NEURAL.lml,
                contact: NEURAL.sml,
                entity: NEURAL.semantic,
              };
              const color = typeColors[profile.type] || NEURAL.episodic;

              return (
                <div key={profile.id} className="glass p-4">
                  <div className="flex items-center gap-3 mb-3">
                    <div
                      className="h-8 w-8 rounded-full flex items-center justify-center text-xs font-bold text-white"
                      style={{ backgroundColor: `${color}30`, border: `1px solid ${color}40` }}
                    >
                      {profile.name.charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-white">{profile.name}</p>
                      <span
                        className="inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ring-1"
                        style={{ color, backgroundColor: `${color}15`, borderColor: `${color}30` }}
                      >
                        {profile.type}
                      </span>
                    </div>
                  </div>

                  {profile.facts && profile.facts.length > 0 && (
                    <div className="mb-2">
                      <p className="text-[10px] font-medium mb-1" style={{ color: NEURAL.shallow }}>Facts</p>
                      <ul className="space-y-0.5">
                        {profile.facts.slice(0, 3).map((fact, i) => (
                          <li key={i} className="text-xs text-slate-300 truncate">{fact}</li>
                        ))}
                        {profile.facts.length > 3 && (
                          <li className="text-[10px]" style={{ color: NEURAL.forgotten }}>+{profile.facts.length - 3} more</li>
                        )}
                      </ul>
                    </div>
                  )}

                  {profile.relationships && profile.relationships.length > 0 && (
                    <div>
                      <p className="text-[10px] font-medium mb-1" style={{ color: NEURAL.shallow }}>Connections</p>
                      <div className="flex flex-wrap gap-1">
                        {profile.relationships.map((rel, i) => (
                          <span key={i} className="text-[10px] rounded-full px-2 py-0.5" style={{
                            backgroundColor: 'rgba(124,58,237,0.1)',
                            color: NEURAL.medium,
                          }}>
                            {rel.target_name}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
