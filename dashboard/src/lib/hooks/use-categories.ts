import useSWR from "swr";
import { fetcher } from "../api/client";
import type { Category } from "../types/category";

export function useCategories() {
  return useSWR<{ categories: Category[] }>("/v1/categories", fetcher);
}

export function useCategoryTree() {
  return useSWR<{ tree: Category[] }>("/v1/categories/tree", fetcher);
}
