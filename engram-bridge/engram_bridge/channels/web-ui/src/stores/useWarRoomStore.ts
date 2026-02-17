import { create } from "zustand";
import type { WarRoom, WarRoomMessage } from "@/types";

interface WarRoomState {
  rooms: WarRoom[];
  messages: Record<string, WarRoomMessage[]>;
  selectedRoomId: string | null;

  setRooms: (rooms: WarRoom[]) => void;
  addRoom: (room: WarRoom) => void;
  updateRoom: (roomId: string, updates: Partial<WarRoom>) => void;
  setMessages: (roomId: string, msgs: WarRoomMessage[]) => void;
  addMessage: (roomId: string, msg: WarRoomMessage) => void;
  selectRoom: (roomId: string | null) => void;
}

export const useWarRoomStore = create<WarRoomState>((set) => ({
  rooms: [],
  messages: {},
  selectedRoomId: null,

  setRooms: (rooms) => set({ rooms }),

  addRoom: (room) =>
    set((s) => ({
      rooms: s.rooms.some((r) => r.id === room.id)
        ? s.rooms.map((r) => (r.id === room.id ? room : r))
        : [...s.rooms, room],
    })),

  updateRoom: (roomId, updates) =>
    set((s) => ({
      rooms: s.rooms.map((r) =>
        r.id === roomId ? { ...r, ...updates } : r,
      ),
    })),

  setMessages: (roomId, msgs) =>
    set((s) => ({
      messages: { ...s.messages, [roomId]: msgs },
    })),

  addMessage: (roomId, msg) =>
    set((s) => {
      const existing = s.messages[roomId] || [];
      if (existing.some((m) => m.id === msg.id)) return s;
      return {
        messages: { ...s.messages, [roomId]: [...existing, msg] },
        rooms: s.rooms.map((r) =>
          r.id === roomId
            ? { ...r, wr_message_count: (r.wr_message_count || 0) + 1 }
            : r,
        ),
      };
    }),

  selectRoom: (roomId) => set({ selectedRoomId: roomId }),
}));
