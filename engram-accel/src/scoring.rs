use pyo3::prelude::*;
use std::collections::HashMap;

/// Tokenize text: lowercase and split on non-alphanumeric boundaries.
#[pyfunction]
pub fn tokenize(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    let mut tokens = Vec::new();
    let mut current = String::new();

    for ch in lower.chars() {
        if ch.is_alphanumeric() || ch == '_' {
            current.push(ch);
        } else if !current.is_empty() {
            tokens.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }

    tokens
}

/// BM25 scoring for N documents against a single query.
///
/// Each document is a Vec<String> of pre-tokenized terms.
/// Returns a Vec<f64> of BM25 scores, one per document.
#[pyfunction]
pub fn bm25_score_batch(
    query_terms: Vec<String>,
    documents: Vec<Vec<String>>,
    total_docs: usize,
    avg_doc_len: f64,
    k1: f64,
    b: f64,
) -> Vec<f64> {
    if query_terms.is_empty() || documents.is_empty() {
        return vec![0.0; documents.len()];
    }

    let total_docs_f = total_docs as f64;
    let avg_doc_len = if avg_doc_len == 0.0 { 1.0 } else { avg_doc_len };

    // Build document frequency: how many docs contain each query term
    let mut doc_freq: HashMap<&str, usize> = HashMap::new();
    for term in &query_terms {
        let mut count = 0usize;
        for doc in &documents {
            if doc.iter().any(|t| t == term) {
                count += 1;
            }
        }
        doc_freq.insert(term.as_str(), count);
    }

    let mut scores = Vec::with_capacity(documents.len());

    for doc in &documents {
        if doc.is_empty() {
            scores.push(0.0);
            continue;
        }

        // Term frequencies in this document
        let mut term_freq: HashMap<&str, usize> = HashMap::new();
        for t in doc {
            *term_freq.entry(t.as_str()).or_insert(0) += 1;
        }

        let doc_len = doc.len() as f64;
        let mut score = 0.0_f64;

        for term in &query_terms {
            let tf = match term_freq.get(term.as_str()) {
                Some(&f) => f as f64,
                None => continue,
            };

            let df = *doc_freq.get(term.as_str()).unwrap_or(&1) as f64;

            // IDF with smoothing
            let idf = ((total_docs_f - df + 0.5) / (df + 0.5) + 1.0).ln();

            // TF with saturation and length normalization
            let tf_component = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * doc_len / avg_doc_len));

            score += idf * tf_component;
        }

        scores.push(score);
    }

    scores
}
