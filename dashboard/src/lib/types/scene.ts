export interface Scene {
  id: string;
  title?: string;
  topic?: string;
  summary?: string;
  start_time: string;
  end_time?: string;
  memory_ids: string[];
  participants?: string[];
  memory_count: number;
}
