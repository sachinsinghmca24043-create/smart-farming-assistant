
"use client";

import { useCallback, useEffect, useState } from "react";

type FeedbackStats = {
  total_feedback: number;
  positive: number;
  negative: number;
  positive_percentage: number;
};

type UsageStats = {
  questions_total: number;
  sessions_total: number;
  flagged_total: number;
  flagged_by_status: Record<string, number>;
  flag_rate: number;
  review_threshold: number;
};

type FlaggedAnswer = {
  id: number;
  created_at: string;
  question: string;
  answer: string;
  intent: string | null;
  language: string | null;
  score: number;
  reasons: string[];
  status: string;
  reviewer_note: string | null;
};

type ReviewStatus = "open" | "reviewed" | "dismissed" | "all";
type FarmerRow = { id: number; name: string; email: string; is_active: number; created_at: string; sessions_count: number; messages_count: number; total_tokens: number };
type QueryRow = { id: number; question: string; language: string | null; intent: string | null; created_at: string; farmer_name: string | null; farmer_email: string | null };
type TokenUsage = { totals: { requests: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }; by_model: Array<{ provider: string; model: string; requests: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }>; daily: Array<{ day: string; total_tokens: number }> };


type KnowledgeRecord = {
  id: string;
  crop: string;
  topic: string;
  content: string;
  keywords: string[];
  language: "en" | "hi" | "kn";
  source_name: string;
  source_url: string;
  verified: boolean;
  scope_note: string;
  translations: Record<string, string>;
  translation_note: string;
};

const emptyKnowledgeRecord: KnowledgeRecord = {
  id: "",
  crop: "",
  topic: "",
  content: "",
  keywords: [],
  language: "en",
  source_name: "",
  source_url: "",
  verified: false,
  scope_note: "",
  translations: {},
  translation_note: "",
};


export default function AdminPage() {
  const [feedback, setFeedback] = useState<FeedbackStats | null>(null);
  const [farmers, setFarmers] = useState<FarmerRow[]>([]);
  const [queries, setQueries] = useState<QueryRow[]>([]);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const [monitoringError, setMonitoringError] = useState("");
  const [usage, setUsage] = useState<UsageStats | null>(null);
  const [flags, setFlags] = useState<FlaggedAnswer[]>([]);
  const [filter, setFilter] = useState<ReviewStatus>("open");

  const [loading, setLoading] = useState(true);
  const [queueLoading, setQueueLoading] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const [knowledge, setKnowledge] = useState<KnowledgeRecord[]>([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState("");
  const [knowledgeMessage, setKnowledgeMessage] = useState("");

  const [showKnowledgeForm, setShowKnowledgeForm] = useState(false);
  const [editingKnowledge, setEditingKnowledge] = useState(false);

  const [knowledgeForm, setKnowledgeForm] =
    useState<KnowledgeRecord>(emptyKnowledgeRecord);

  const [savingKnowledge, setSavingKnowledge] = useState(false); 

  const loadDashboard = useCallback(async () => {
    setError("");

    try {
      const [feedbackResponse, usageResponse] = await Promise.all([
        fetch("/api/admin/feedback-stats"),
        fetch("/api/admin/stats"),
      ]);

      if (!feedbackResponse.ok || !usageResponse.ok) {
        throw new Error("Unable to load admin analytics.");
      }

      const feedbackResult = await feedbackResponse.json();
      const usageResult = await usageResponse.json();

      setFeedback(feedbackResult.data);
      setUsage(usageResult);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load admin analytics."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFlags = useCallback(async () => {
    setQueueLoading(true);
    setActionError("");

    try {
      const response = await fetch(
        `/api/admin/flagged?status=${filter}&limit=50`
      );

      if (!response.ok) {
        throw new Error("Unable to load the review queue.");
      }

      const result = await response.json();
      setFlags(result.flagged);
    } catch (err) {
      setActionError(
        err instanceof Error
          ? err.message
          : "Unable to load review queue."
      );
    } finally {
      setQueueLoading(false);
    }
  }, [filter]);


  const loadKnowledge = useCallback(async () => {
  setKnowledgeLoading(true);
  setKnowledgeError("");

  try {
    const response = await fetch("/api/admin/knowledge", {
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error("Unable to load knowledge base.");
    }

    const result = await response.json();
    setKnowledge(result.records ?? []);
  } catch (err) {
    setKnowledgeError(
      err instanceof Error
        ? err.message
        : "Unable to load knowledge base."
    );
  } finally {
    setKnowledgeLoading(false);
  }
}, []);


  useEffect(() => {
  void loadKnowledge();
}, [loadKnowledge]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  const loadMonitoring = useCallback(async () => {
    setMonitoringError("");
    try {
      const [farmerRes, queryRes, tokenRes] = await Promise.all([
        fetch("/api/admin/farmers?limit=100", { cache: "no-store" }),
        fetch("/api/admin/queries?limit=100", { cache: "no-store" }),
        fetch("/api/admin/token-usage", { cache: "no-store" }),
      ]);
      if (!farmerRes.ok || !queryRes.ok || !tokenRes.ok) throw new Error("Unable to load farmer or token monitoring data.");
      const [farmerData, queryData, tokenData] = await Promise.all([farmerRes.json(), queryRes.json(), tokenRes.json()]);
      setFarmers(farmerData.farmers ?? []);
      setQueries(queryData.queries ?? []);
      setTokenUsage(tokenData);
    } catch (err) {
      setMonitoringError(err instanceof Error ? err.message : "Monitoring data unavailable.");
    }
  }, []);

  useEffect(() => { void loadMonitoring(); }, [loadMonitoring]);

  useEffect(() => {
    void loadFlags();
  }, [loadFlags]);

  async function reviewAnswer(
    id: number,
    status: "reviewed" | "dismissed"
  ) {
    setUpdatingId(id);
    setActionError("");

    try {
      const response = await fetch(
        `/api/admin/flagged/${id}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status,
            note: status === "reviewed"
              ? "Reviewed by extension worker."
              : "Dismissed after review.",
          }),
        }
      );

      if (!response.ok) {
        throw new Error("Unable to update this review item.");
      }

      await Promise.all([loadFlags(), loadDashboard()]);
    } catch (err) {
      setActionError(
        err instanceof Error
          ? err.message
          : "Unable to update review status."
      );
    } finally {
      setUpdatingId(null);
    }
  }

  function openAddKnowledgeForm() {
  setKnowledgeForm(emptyKnowledgeRecord);
  setEditingKnowledge(false);
  setKnowledgeMessage("");
  setShowKnowledgeForm(true);
}

function openEditKnowledgeForm(record: KnowledgeRecord) {
  setKnowledgeForm({
    ...record,
    keywords: record.keywords ?? [],
    translations: record.translations ?? {},
  });

  setEditingKnowledge(true);
  setKnowledgeMessage("");
  setShowKnowledgeForm(true);
}

async function saveKnowledgeRecord(
  event: React.FormEvent<HTMLFormElement>
) {
  event.preventDefault();

  setSavingKnowledge(true);
  setKnowledgeError("");
  setKnowledgeMessage("");

  try {
    const url = editingKnowledge
      ? `/api/admin/knowledge/${encodeURIComponent(knowledgeForm.id)}`
      : "/api/admin/knowledge";

    const response = await fetch(url, {
      method: editingKnowledge ? "PUT" : "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(knowledgeForm),
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(
        result.detail || "Unable to save knowledge record."
      );
    }

    setShowKnowledgeForm(false);
    setKnowledgeForm(emptyKnowledgeRecord);

    setKnowledgeMessage(
      editingKnowledge
        ? "Knowledge record updated successfully."
        : "Knowledge record added successfully."
    );

    await loadKnowledge();
  } catch (err) {
    setKnowledgeError(
      err instanceof Error
        ? err.message
        : "Unable to save knowledge record."
    );
  } finally {
    setSavingKnowledge(false);
  }
}

async function deleteKnowledgeRecord(id: string) {
  if (!window.confirm(`Delete knowledge record "${id}"?`)) {
    return;
  }

  setKnowledgeError("");
  setKnowledgeMessage("");

  try {
    const response = await fetch(
      `/api/admin/knowledge/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    );

    if (!response.ok) {
      throw new Error("Unable to delete knowledge record.");
    }

    setKnowledgeMessage("Knowledge record deleted successfully.");
    await loadKnowledge();
  } catch (err) {
    setKnowledgeError(
      err instanceof Error ? err.message : "Delete failed."
    );
  }
}

  return (
    <main className="min-h-screen bg-[#f6faf6] p-6 sm:p-10">
      <div className="mx-auto max-w-6xl">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-slate-800">
              Smart Farming Admin Dashboard
            </h1>
            <p className="mt-2 text-slate-500">
              Monitor chatbot usage, farmer satisfaction and response quality.
            </p>
          </div>

          <button
            type="button"
            onClick={() => {
              void loadDashboard();
              void loadFlags();
              void loadKnowledge();
              void loadMonitoring();
            }}
            className="rounded-xl border border-green-200 bg-white px-4 py-2 text-sm font-medium text-green-800 hover:bg-green-50"
          >
            Refresh dashboard
          </button>
        </div>

        {loading && (
          <p className="mt-8 text-slate-600">
            Loading analytics...
          </p>
        )}

        {error && (
          <p role="alert" className="mt-6 rounded-xl bg-red-50 p-4 text-red-700">
            {error}
          </p>
        )}

        {usage && (
          <section className="mt-8">
            <h2 className="mb-4 text-xl font-semibold text-slate-800">
              Chatbot Usage Analytics
            </h2>

            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard title="Total Questions" value={usage.questions_total} />
              <StatCard title="Total Sessions" value={usage.sessions_total} />
              <StatCard title="Flagged Answers" value={usage.flagged_total} />
              <StatCard
  title="Open Review Items"
  value={usage.flagged_by_status.open ?? 0}
/>
            </div>
          </section>
        )}

        {feedback && (
          <section className="mt-10">
            <h2 className="mb-4 text-xl font-semibold text-slate-800">
              Farmer Feedback Analytics
            </h2>

            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard title="Total Feedback" value={feedback.total_feedback} />
              <StatCard title="Positive Ratings" value={feedback.positive} />
              <StatCard title="Negative Ratings" value={feedback.negative} />
              <StatCard
                title="Positive Rate"
  value={`${feedback.positive_percentage}%`}
              />
            </div>

            <section className="mt-5 rounded-2xl border border-green-100 bg-white p-6 shadow-sm">
              <h3 className="text-lg font-semibold text-slate-800">
                Farmer Satisfaction
              </h3>

              <div className="mt-5 h-4 overflow-hidden rounded-full bg-red-100">
                <div
                  className="h-full rounded-full bg-green-600"
                  style={{
                    width: `${Math.min(
                      100,
                      Math.max(0, feedback.positive_percentage)
                    )}%`,
                  }}
                />
              </div>

              <p className="mt-3 text-sm text-slate-500">
                {feedback.positive} positive out of{" "}
                {feedback.total_feedback} total responses.
              </p>
            </section>
          </section>
        )}



{/* FARMER AND MODEL MONITORING */}
<section className="mt-10 space-y-8">
  <div>
    <h2 className="text-xl font-semibold text-slate-800">LLM Token Consumption</h2>
    <p className="mt-1 text-sm text-slate-500">Provider-reported Gemini token usage recorded for uncached model calls.</p>
    {monitoringError && <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{monitoringError}</p>}
    {tokenUsage && <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard title="LLM Requests" value={tokenUsage.totals.requests} />
      <StatCard title="Prompt Tokens" value={tokenUsage.totals.prompt_tokens} />
      <StatCard title="Completion Tokens" value={tokenUsage.totals.completion_tokens} />
      <StatCard title="Total Tokens" value={tokenUsage.totals.total_tokens} />
    </div>}
    {tokenUsage && <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="w-full min-w-[600px] text-left text-sm"><thead className="bg-slate-50"><tr><th className="p-3">Provider</th><th className="p-3">Model</th><th className="p-3">Requests</th><th className="p-3">Total tokens</th></tr></thead>
      <tbody>{tokenUsage.by_model.map((row) => <tr key={`${row.provider}-${row.model}`} className="border-t"><td className="p-3">{row.provider}</td><td className="p-3">{row.model}</td><td className="p-3">{row.requests}</td><td className="p-3">{row.total_tokens}</td></tr>)}</tbody></table>
      {tokenUsage.by_model.length === 0 && <p className="p-4 text-slate-500">No LLM token usage has been recorded yet.</p>}
    </div>}
  </div>
  <div>
    <h2 className="text-xl font-semibold text-slate-800">Registered Farmers</h2>
    <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white"><table className="w-full min-w-[700px] text-left text-sm">
      <thead className="bg-slate-50"><tr><th className="p-3">Farmer</th><th className="p-3">Email</th><th className="p-3">Registered</th><th className="p-3">Sessions</th><th className="p-3">Messages</th><th className="p-3">Status</th></tr></thead>
      <tbody>{farmers.map((farmer) => <tr key={farmer.id} className="border-t"><td className="p-3">{farmer.name}</td><td className="p-3">{farmer.email}</td><td className="p-3">{farmer.created_at}</td><td className="p-3">{farmer.sessions_count}</td><td className="p-3">{farmer.messages_count}</td><td className="p-3">{farmer.is_active ? "Active" : "Inactive"}</td></tr>)}</tbody>
      </table>{farmers.length === 0 && <p className="p-4 text-slate-500">No farmer records found.</p>}</div>
  </div>
  <div>
    <h2 className="text-xl font-semibold text-slate-800">Recent Farmer Queries</h2>
    <div className="mt-4 space-y-3">{queries.map((query) => <article key={query.id} className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="font-medium text-slate-800">{query.question}</p><p className="mt-2 text-xs text-slate-500">{query.farmer_name || "Unassigned farmer"} · {query.farmer_email || "No email"} · {query.language || "Unknown language"} · {query.intent || "Unclassified"} · {query.created_at}</p>
    </article>)}{queries.length === 0 && <p className="text-sm text-slate-500">No farmer queries found.</p>}</div>
  </div>
</section>

{/* KNOWLEDGE BASE MANAGEMENT */}
<section className="mt-10">
  <div className="flex flex-wrap items-center justify-between gap-4">
    <div>
      <h2 className="text-xl font-semibold text-slate-800">
        Agricultural Knowledge Base
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Manage verified agricultural guidance and information.
      </p>
    </div>

    <button
      type="button"
      onClick={openAddKnowledgeForm}
      className="rounded-xl bg-green-700 px-4 py-2 text-sm font-medium text-white hover:bg-green-800"
    >
      + Add New Record
    </button>
  </div>

  {knowledgeMessage && (
    <p className="mt-4 rounded-xl bg-green-50 p-3 text-sm text-green-700">
      {knowledgeMessage}
    </p>
  )}

  {knowledgeError && (
    <p role="alert" className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">
      {knowledgeError}
    </p>
  )}

  {knowledgeLoading && (
    <p className="mt-5 text-slate-500">Loading knowledge records...</p>
  )}

  {!knowledgeLoading && (
    <div className="mt-5 overflow-x-auto rounded-2xl border border-green-100 bg-white shadow-sm">
      <table className="w-full min-w-[750px] text-left text-sm">
        <thead className="bg-green-50 text-slate-700">
          <tr>
            <th className="p-4">Crop</th>
            <th className="p-4">Topic</th>
            <th className="p-4">Language</th>
            <th className="p-4">Verification</th>
            <th className="p-4">Actions</th>
          </tr>
        </thead>

        <tbody>
          {knowledge.map((record) => (
            <tr key={record.id} className="border-t border-slate-100">
              <td className="p-4 font-medium">{record.crop}</td>
              <td className="p-4">{record.topic}</td>
              <td className="p-4 uppercase">{record.language}</td>
              <td className="p-4">
                {record.verified ? (
                  <span className="rounded-lg bg-green-100 px-2 py-1 text-green-700">
                    Verified
                  </span>
                ) : (
                  <span className="rounded-lg bg-amber-100 px-2 py-1 text-amber-700">
                    Unverified
                  </span>
                )}
              </td>
              <td className="p-4">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => openEditKnowledgeForm(record)}
                    className="rounded-lg border border-blue-200 px-3 py-1 text-blue-700 hover:bg-blue-50"
                  >
                    Edit
                  </button>

                  <button
                    type="button"
                    onClick={() => void deleteKnowledgeRecord(record.id)}
                    className="rounded-lg border border-red-200 px-3 py-1 text-red-700 hover:bg-red-50"
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {knowledge.length === 0 && (
        <p className="p-8 text-center text-slate-500">
          No knowledge records found.
        </p>
      )}
    </div>
  )}

  {/* ADD / EDIT FORM */}
  {showKnowledgeForm && (
    <div className="mt-6 rounded-2xl border border-green-100 bg-white p-6 shadow-sm">
      <h3 className="text-lg font-semibold text-slate-800">
        {editingKnowledge ? "Edit Knowledge Record" : "Add Knowledge Record"}
      </h3>

      <form onSubmit={saveKnowledgeRecord} className="mt-5 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium">Record ID</label>
            <input
              required
              disabled={editingKnowledge}
              value={knowledgeForm.id}
              onChange={(e) =>
                setKnowledgeForm({ ...knowledgeForm, id: e.target.value })
              }
              className="w-full rounded-xl border border-slate-300 px-3 py-2 disabled:bg-slate-100"
              placeholder="rice_irrigation_01"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Crop</label>
            <input
              required
              value={knowledgeForm.crop}
              onChange={(e) =>
                setKnowledgeForm({ ...knowledgeForm, crop: e.target.value })
              }
              className="w-full rounded-xl border border-slate-300 px-3 py-2"
              placeholder="Rice"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Topic</label>
            <input
              required
              value={knowledgeForm.topic}
              onChange={(e) =>
                setKnowledgeForm({ ...knowledgeForm, topic: e.target.value })
              }
              className="w-full rounded-xl border border-slate-300 px-3 py-2"
              placeholder="Irrigation"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Language</label>
            <select
              value={knowledgeForm.language}
              onChange={(e) =>
                setKnowledgeForm({
                  ...knowledgeForm,
                  language: e.target.value as "en" | "hi" | "kn",
                })
              }
              className="w-full rounded-xl border border-slate-300 px-3 py-2"
            >
              <option value="en">English</option>
              <option value="hi">Hindi</option>
              <option value="kn">Kannada</option>
            </select>
          </div>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">Content</label>
          <textarea
            required
            rows={5}
            value={knowledgeForm.content}
            onChange={(e) =>
              setKnowledgeForm({ ...knowledgeForm, content: e.target.value })
            }
            className="w-full rounded-xl border border-slate-300 px-3 py-2"
            placeholder="Enter agricultural guidance..."
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">
            Keywords (comma-separated)
          </label>
          <input
            value={knowledgeForm.keywords.join(", ")}
            onChange={(e) =>
              setKnowledgeForm({
                ...knowledgeForm,
                keywords: e.target.value.split(",").map((word) => word.trim()).filter(Boolean),
              })
            }
            className="w-full rounded-xl border border-slate-300 px-3 py-2"
            placeholder="rice, water, irrigation"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">Source Name</label>
          <input
            value={knowledgeForm.source_name}
            onChange={(e) =>
              setKnowledgeForm({ ...knowledgeForm, source_name: e.target.value })
            }
            className="w-full rounded-xl border border-slate-300 px-3 py-2"
            placeholder="ICAR"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">Source URL</label>
          <input
            type="url"
            value={knowledgeForm.source_url}
            onChange={(e) =>
              setKnowledgeForm({ ...knowledgeForm, source_url: e.target.value })
            }
            className="w-full rounded-xl border border-slate-300 px-3 py-2"
            placeholder="https://..."
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">Scope Note</label>
          <textarea
            rows={2}
            value={knowledgeForm.scope_note}
            onChange={(e) =>
              setKnowledgeForm({ ...knowledgeForm, scope_note: e.target.value })
            }
            className="w-full rounded-xl border border-slate-300 px-3 py-2"
            placeholder="Region, crop stage, or limitations..."
          />
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={knowledgeForm.verified}
            onChange={(e) =>
              setKnowledgeForm({ ...knowledgeForm, verified: e.target.checked })
            }
          />
          Mark this record as verified
        </label>

        <div className="flex flex-wrap gap-3 pt-2">
          <button
            type="submit"
            disabled={savingKnowledge}
            className="rounded-xl bg-green-700 px-5 py-2 font-medium text-white hover:bg-green-800 disabled:opacity-50"
          >
            {savingKnowledge ? "Saving..." : editingKnowledge ? "Update Record" : "Save Record"}
          </button>

          <button
            type="button"
            onClick={() => setShowKnowledgeForm(false)}
            className="rounded-xl border border-slate-300 px-5 py-2 text-slate-700 hover:bg-slate-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  )}
</section>


        <section className="mt-10">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold text-slate-800">
                Unanswered & Low-Confidence Queries
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Review AI responses flagged for extension-worker attention.
              </p>
            </div>

            <select
              value={filter}
              onChange={(event) =>
                setFilter(event.target.value as ReviewStatus)
              }
              className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm"
            >
              <option value="open">Pending review</option>
              <option value="reviewed">Reviewed</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All queries</option>
            </select>
          </div>

          {usage && (
            <div className="mt-4 flex flex-wrap gap-3 text-sm">
              <span className="rounded-lg bg-amber-100 px-3 py-2 text-amber-800">
                Open: {usage.flagged_by_status.open ?? 0}
              </span>
              <span className="rounded-lg bg-green-100 px-3 py-2 text-green-800">
                Reviewed: {usage.flagged_by_status.reviewed ?? 0}
              </span>
              <span className="rounded-lg bg-slate-200 px-3 py-2 text-slate-700">
                Dismissed: {usage.flagged_by_status.dismissed ?? 0}
              </span>
            </div>
          )}

          {actionError && (
            <p role="alert" className="mt-4 text-sm text-red-600">
              {actionError}
            </p>
          )}

          {queueLoading && (
            <p className="mt-6 text-slate-500">
              Loading review queue...
            </p>
          )}

          {!queueLoading && flags.length === 0 && (
            <div className="mt-5 rounded-2xl border border-slate-200 bg-white p-8 text-center">
              <p className="font-medium text-slate-700">
                No queries found in this category.
              </p>
              <p className="mt-1 text-sm text-slate-500">
                Flagged answers will appear here when they require review.
              </p>
            </div>
          )}

          <div className="mt-5 space-y-5">
            {flags.map((item) => (
              <article
                key={item.id}
                className="rounded-2xl border border-amber-100 bg-white p-6 shadow-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-lg bg-amber-100 px-3 py-1 font-medium text-amber-800">
                      Confidence: {(item.score * 100).toFixed(1)}%
                    </span>
                    <span className="rounded-lg bg-slate-100 px-3 py-1 text-slate-600">
                      {item.status}
                    </span>
                    {item.language && (
                      <span className="rounded-lg bg-blue-50 px-3 py-1 text-blue-700">
                        {item.language}
                      </span>
                    )}
                  </div>

                  <span className="text-xs text-slate-400">
                    {new Date(item.created_at).toLocaleString()}
                  </span>
                </div>

                <h3 className="mt-5 text-sm font-semibold text-slate-700">
                  Farmer&apos;s question
                </h3>
                <p className="mt-2 whitespace-pre-wrap text-slate-800">
                  {item.question}
                </p>

                <h3 className="mt-5 text-sm font-semibold text-slate-700">
                  Assistant&apos;s answer
                </h3>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-600">
                  {item.answer}
                </p>

                {item.reasons.length > 0 && (
                  <div className="mt-4">
                    <p className="text-sm font-semibold text-slate-700">
                      Review reasons
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {item.reasons.map((reason) => (
                        <span
                          key={reason}
                          className="rounded-lg bg-red-50 px-3 py-1 text-xs text-red-700"
                        >
                          {reason.replaceAll("_", " ")}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {item.intent && (
                  <p className="mt-3 text-xs text-slate-500">
                    Intent: {item.intent}
                  </p>
                )}

                {item.reviewer_note && (
                  <p className="mt-3 text-sm text-slate-600">
                    Reviewer note: {item.reviewer_note}
                  </p>
                )}

                {item.status === "open" && (
                  <div className="mt-5 flex flex-wrap gap-3">
                    <button
                      type="button"
                      disabled={updatingId === item.id}
                      onClick={() => void reviewAnswer(item.id, "reviewed")}
                      className="rounded-xl bg-green-700 px-4 py-2 text-sm font-medium text-white hover:bg-green-800 disabled:opacity-50"
                    >
                      {updatingId === item.id
                        ? "Updating..."
                        : "Mark as reviewed"}
                    </button>

                    <button
                      type="button"
                      disabled={updatingId === item.id}
                      onClick={() => void reviewAnswer(item.id, "dismissed")}
                      className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      Dismiss
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

function StatCard({
  title,
  value,
}: {
  title: string;
  value: string | number;
}) {
  return (
    <div className="rounded-2xl border border-green-100 bg-white p-6 shadow-sm">
      <p className="text-sm text-slate-500">{title}</p>
      <p className="mt-3 text-3xl font-bold text-green-800">{value}</p>
    </div>
  );
}
