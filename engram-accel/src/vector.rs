use pyo3::prelude::*;
use rayon::prelude::*;

/// Cosine similarity between two vectors.
#[pyfunction]
pub fn cosine_similarity(a: Vec<f64>, b: Vec<f64>) -> f64 {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return 0.0;
    }
    let mut dot = 0.0_f64;
    let mut norm_a = 0.0_f64;
    let mut norm_b = 0.0_f64;
    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }
    let denom = norm_a.sqrt() * norm_b.sqrt();
    if denom == 0.0 {
        return 0.0;
    }
    let result = dot / denom;
    if result.is_nan() || result.is_infinite() {
        0.0
    } else {
        result
    }
}

/// Compute cosine similarity of one query vector against N stored vectors.
/// The loop runs in Rust with rayon parallelism for large batches.
#[pyfunction]
pub fn cosine_similarity_batch(query: Vec<f64>, store: Vec<Vec<f64>>) -> Vec<f64> {
    if query.is_empty() || store.is_empty() {
        return vec![0.0; store.len()];
    }

    // Pre-compute query norm once
    let query_norm_sq: f64 = query.iter().map(|x| x * x).sum();
    let query_norm = query_norm_sq.sqrt();
    if query_norm == 0.0 {
        return vec![0.0; store.len()];
    }

    let threshold = 256; // use rayon only for larger batches
    if store.len() < threshold {
        store
            .iter()
            .map(|vec| cosine_sim_with_prenorm(&query, query_norm, vec))
            .collect()
    } else {
        store
            .par_iter()
            .map(|vec| cosine_sim_with_prenorm(&query, query_norm, vec))
            .collect()
    }
}

#[inline]
fn cosine_sim_with_prenorm(query: &[f64], query_norm: f64, vec: &[f64]) -> f64 {
    if vec.len() != query.len() {
        return 0.0;
    }
    let mut dot = 0.0_f64;
    let mut norm_b = 0.0_f64;
    for (x, y) in query.iter().zip(vec.iter()) {
        dot += x * y;
        norm_b += y * y;
    }
    let denom = query_norm * norm_b.sqrt();
    if denom == 0.0 {
        return 0.0;
    }
    let result = dot / denom;
    if result.is_nan() || result.is_infinite() {
        0.0
    } else {
        result
    }
}
