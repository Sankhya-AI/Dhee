import { get } from "./client";
import type { Category } from "../types/category";

export function listCategories(): Promise<{ categories: Category[] }> {
  return get("/v1/categories");
}

export function getCategoryTree(): Promise<{ tree: Category[] }> {
  return get("/v1/categories/tree");
}

export function getCategorySummary(
  id: string,
  regenerate = false
): Promise<{ category_id: string; summary: string }> {
  return get(`/v1/categories/${id}/summary?regenerate=${regenerate}`);
}
