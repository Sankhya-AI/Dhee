use pyo3::prelude::*;

mod decay;
mod scoring;
mod vector;

/// engram_accel — Rust acceleration for the Engram memory layer.
#[pymodule]
fn engram_accel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Vector operations
    m.add_function(wrap_pyfunction!(vector::cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(vector::cosine_similarity_batch, m)?)?;

    // Decay math
    m.add_function(wrap_pyfunction!(decay::calculate_decayed_strength, m)?)?;
    m.add_function(wrap_pyfunction!(decay::decay_traces_batch, m)?)?;

    // Scoring
    m.add_function(wrap_pyfunction!(scoring::bm25_score_batch, m)?)?;
    m.add_function(wrap_pyfunction!(scoring::tokenize, m)?)?;

    Ok(())
}
