/**
 * Dhee JavaScript SDK — 4 methods, mirrors the MCP tools.
 *
 * Usage:
 *   const { Dhee } = require('dhee');
 *   const d = new Dhee({ baseUrl: 'http://localhost:8100' });
 *   await d.remember("User prefers TypeScript");
 *   const results = await d.recall("programming preferences");
 *   const ctx = await d.context("fixing auth bug");
 *   await d.checkpoint({ summary: "Fixed the bug" });
 */

class Dhee {
  constructor({ baseUrl = 'http://localhost:8100', userId = 'default' } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.userId = userId;
  }

  /** Store a fact or preference. 0 LLM calls, 1 embed. */
  async remember(content, { userId } = {}) {
    return this._post('/api/v1/remember', {
      content,
      user_id: userId || this.userId,
    });
  }

  /** Search memory, get top-K results. 0 LLM calls, 1 embed. */
  async recall(query, { limit = 5, userId } = {}) {
    return this._post('/api/v1/recall', {
      query,
      user_id: userId || this.userId,
      limit,
    });
  }

  /** HyperAgent bootstrap — get everything at session start. */
  async context({ taskDescription, userId } = {}) {
    return this._post('/api/v1/context', {
      task_description: taskDescription,
      user_id: userId || this.userId,
    });
  }

  /** Save session + enrich + reflect + record outcome. */
  async checkpoint({
    summary,
    status = 'paused',
    taskType,
    outcomeScore,
    whatWorked,
    whatFailed,
    keyDecision,
    rememberTo,
    triggerKeywords,
    decisions,
    todos,
    filesTouched,
    repo,
    userId,
  }) {
    return this._post('/api/v1/checkpoint', {
      summary,
      status,
      task_type: taskType,
      outcome_score: outcomeScore,
      what_worked: whatWorked,
      what_failed: whatFailed,
      key_decision: keyDecision,
      remember_to: rememberTo,
      trigger_keywords: triggerKeywords,
      decisions,
      todos,
      files_touched: filesTouched,
      repo,
      user_id: userId || this.userId,
    });
  }

  async _post(path, body) {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`Dhee API error: ${response.status} ${response.statusText}`);
    }
    return response.json();
  }
}

module.exports = { Dhee };
