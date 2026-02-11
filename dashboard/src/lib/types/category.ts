export interface Category {
  id: string;
  name: string;
  parent_id?: string;
  memory_count: number;
  strength: number;
  keywords?: string[];
  children?: Category[];
}

export interface CategoryTree {
  tree: Category[];
}
