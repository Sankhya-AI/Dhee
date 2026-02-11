export interface Profile {
  id: string;
  name: string;
  type: "self" | "contact" | "entity";
  user_id: string;
  facts?: string[];
  preferences?: string[];
  relationships?: ProfileRelationship[];
  created_at: string;
  updated_at: string;
}

export interface ProfileRelationship {
  target_id: string;
  target_name: string;
  relationship_type: string;
}
