use pyo3::prelude::*;

/// Calculate decayed strength for a single memory.
///
/// Formula: strength * exp(-rate * elapsed_days / (1 + factor * ln(1 + access_count)))
/// Result is clamped to [0.0, 1.0].
#[pyfunction]
pub fn calculate_decayed_strength(
    strength: f64,
    elapsed_days: f64,
    decay_rate: f64,
    access_count: u32,
    dampening_factor: f64,
) -> f64 {
    if strength.is_nan() {
        return 0.0;
    }
    let dampening = 1.0 + dampening_factor * (1.0 + access_count as f64).ln();
    let decayed = strength * (-decay_rate * elapsed_days / dampening).exp();
    decayed.clamp(0.0, 1.0)
}

/// Batch decay for multi-trace strength values.
///
/// Each trace is (s_fast, s_mid, s_slow). Returns decayed traces.
/// Uses per-trace decay rates and shared dampening formula.
#[pyfunction]
pub fn decay_traces_batch(
    traces: Vec<(f64, f64, f64)>,
    elapsed_days: Vec<f64>,
    access_counts: Vec<u32>,
    fast_rate: f64,
    mid_rate: f64,
    slow_rate: f64,
) -> Vec<(f64, f64, f64)> {
    let n = traces.len();
    let mut results = Vec::with_capacity(n);

    for i in 0..n {
        let (s_fast, s_mid, s_slow) = traces[i];
        let days = if i < elapsed_days.len() {
            elapsed_days[i]
        } else {
            0.0
        };
        let access = if i < access_counts.len() {
            access_counts[i]
        } else {
            0
        };

        let dampening = 1.0 + 0.5 * (1.0 + access as f64).ln();

        let new_fast = (s_fast * (-fast_rate * days / dampening).exp()).clamp(0.0, 1.0);
        let new_mid = (s_mid * (-mid_rate * days / dampening).exp()).clamp(0.0, 1.0);
        let new_slow = (s_slow * (-slow_rate * days / dampening).exp()).clamp(0.0, 1.0);

        results.push((new_fast, new_mid, new_slow));
    }

    results
}
