/** Turns a gateway tool call into the words the UI shows.
 *
 *  A tool name is `<server>__<verb>_<noun...>`. The verb picks the icon and the
 *  tense pair, the noun and a few well-known arguments make the label concrete:
 *  "Reading order #1043", then "Read order #1043". */

export type StepKind = "search" | "read" | "edit" | "create" | "truck" | "package" | "shell" | "plan" | "list";

export interface ToolWords {
  kind: StepKind;
  server: string; // slug
  base: string; // tool name without the server prefix
  verb: string; // present progressive, e.g. "Searching"
  past: string; // "Searched"
  noun: string; // "orders"
  argument: string; // "placed today", "#1043", "" when none
  changesData: boolean; // best guess from the verb; the gateway's annotations win when present
}

const VERBS: Record<string, { ing: string; past: string; kind: StepKind; write?: boolean }> = {
  list: { ing: "Listing", past: "Listed", kind: "search" },
  search: { ing: "Searching", past: "Searched", kind: "search" },
  find: { ing: "Finding", past: "Found", kind: "search" },
  get: { ing: "Reading", past: "Read", kind: "read" },
  read: { ing: "Reading", past: "Read", kind: "read" },
  fetch: { ing: "Fetching", past: "Fetched", kind: "read" },
  quote: { ing: "Quoting", past: "Quoted", kind: "truck" },
  track: { ing: "Tracking", past: "Tracked", kind: "truck" },
  create: { ing: "Creating", past: "Created", kind: "create", write: true },
  add: { ing: "Adding", past: "Added", kind: "create", write: true },
  update: { ing: "Updating", past: "Updated", kind: "edit", write: true },
  set: { ing: "Setting", past: "Set", kind: "edit", write: true },
  change: { ing: "Changing", past: "Changed", kind: "edit", write: true },
  delete: { ing: "Deleting", past: "Deleted", kind: "edit", write: true },
  remove: { ing: "Removing", past: "Removed", kind: "edit", write: true },
  restock: { ing: "Restocking", past: "Restocked", kind: "package", write: true },
  cancel: { ing: "Cancelling", past: "Cancelled", kind: "edit", write: true },
  refund: { ing: "Refunding", past: "Refunded", kind: "edit", write: true },
  send: { ing: "Sending", past: "Sent", kind: "shell", write: true },
  run: { ing: "Running", past: "Ran", kind: "shell", write: true },
};

const ID_KEYS = ["order_id", "product_id", "purchase_order_id", "po_number", "tracking_number", "id", "order"];
const ID_NOUNS: Record<string, string> = { order_id: "order", product_id: "product", purchase_order_id: "purchase order", po_number: "purchase order", tracking_number: "parcel", order: "order" };
const singularOf = (noun: string) => (noun.endsWith("s") && !noun.endsWith("ss") && !noun.endsWith("us") ? noun.slice(0, -1) : noun);
const NAME_KEYS = ["name", "title", "sku", "carrier", "query", "search", "email"];

export function splitTool(publicName: string): { server: string; base: string } {
  const i = publicName.indexOf("__");
  return i > 0 ? { server: publicName.slice(0, i), base: publicName.slice(i + 2) } : { server: "", base: publicName };
}

function nounFor(parts: string[], server: string): string {
  let noun = parts.join(" ");
  if (server === "shipping" && (noun === "shipping" || noun === "")) noun = "shipping quotes";
  if (noun === "carriers") noun = "carriers";
  if (noun === "order status") noun = "order status";
  return noun;
}

export function toolWords(publicName: string, input: Record<string, unknown> | null | undefined, readOnly?: boolean): ToolWords {
  const { server, base } = splitTool(publicName);
  const parts = base.split("_").filter(Boolean);
  const verbKey = parts[0] ?? "";
  const verb = VERBS[verbKey] ?? { ing: "Calling", past: "Called", kind: "shell" as StepKind };
  const noun = nounFor(verbKey && VERBS[verbKey] ? parts.slice(1) : parts, server);
  const args = input && typeof input === "object" ? input : {};
  let argument = "";
  const idKey = ID_KEYS.find((k) => args[k] !== undefined && args[k] !== null && args[k] !== "");
  const nameKey = NAME_KEYS.find((k) => typeof args[k] === "string" && (args[k] as string).trim());
  if (idKey) argument = `${ID_NOUNS[idKey] ?? singularOf(noun)} #${String(args[idKey])}`;
  else if (nameKey === "query" || nameKey === "search") argument = `${noun} matching “${args[nameKey]}”`;
  else if (nameKey) argument = `${args[nameKey]}`;
  else if (typeof args.status === "string") argument = `${args.status} ${noun}`;
  else if (typeof args.after === "string" || typeof args.since === "string" || args.today === true) argument = `${noun} placed today`;
  const changesData = readOnly === undefined ? Boolean(verb.write) : !readOnly;
  return { kind: verb.kind, server, base, verb: verb.ing, past: verb.past, noun, argument, changesData };
}

/** "Searching" + "orders placed today" → live label parts; the UI dims the verb and brightens the argument. */
export function liveParts(w: ToolWords): { verb: string; argument: string } {
  if (w.changesData && (w.kind === "edit" || w.kind === "create")) {
    return { verb: w.kind === "create" ? "Preparing" : "Preparing a change to", argument: w.argument || (w.kind === "create" ? `a new ${w.noun}` : w.noun) };
  }
  if (w.kind === "truck") return w.argument && w.argument !== w.noun ? { verb: "Getting shipping quotes for", argument: w.argument } : { verb: "Getting", argument: "shipping quotes" };
  return { verb: w.verb, argument: w.argument || w.noun };
}

/** Past-tense label, with a count when the result parses as a list. */
export function doneLabel(w: ToolWords, output?: string | null, isError?: boolean): string {
  if (isError) return `${w.past} ${w.argument || w.noun}`.replace(/^Called/, "Failed");
  const count = countIn(output);
  if (w.kind === "truck") {
    const what = w.argument && w.argument !== w.noun ? ` for ${w.argument}` : "";
    return count !== null ? `Got ${count} shipping ${count === 1 ? "quote" : "quotes"}${what}` : `Got shipping quotes${what}`;
  }
  if (w.kind === "search" && count !== null) return `Found ${count} ${count === 1 ? singularOf(w.noun) : w.noun}${w.argument && !w.argument.startsWith(w.noun) ? ` (${w.argument})` : ""}`;
  return `${w.past} ${w.argument || w.noun}`;
}

/** Count items in a tool result preview: a JSON array, or an object with a list under a common key. */
export function countIn(output: string | null | undefined): number | null {
  if (!output) return null;
  const text = output.trim();
  if (!text.startsWith("[") && !text.startsWith("{")) return null;
  try {
    const data = JSON.parse(text.endsWith("…") ? text.slice(0, -1) : text);
    if (Array.isArray(data)) return data.length;
    if (data && typeof data === "object") {
      for (const key of ["items", "orders", "products", "quotes", "carriers", "results", "data"]) {
        const v = (data as Record<string, unknown>)[key];
        if (Array.isArray(v)) return v.length;
      }
      if (typeof (data as { count?: unknown }).count === "number") return (data as { count: number }).count;
      if (typeof (data as { total?: unknown }).total === "number") return (data as { total: number }).total;
    }
  } catch {
    return null;
  }
  return null;
}

/** A one-line description for an audit row, from the stored arguments only. */
export function auditLabel(publicName: string, argumentsJson: string, outcome: string, error: string | null): string {
  let input: Record<string, unknown> | null = null;
  try {
    input = JSON.parse(argumentsJson || "{}");
  } catch {
    input = null;
  }
  const w = toolWords(publicName, input);
  if (outcome === "ok") return doneLabel(w);
  if (outcome === "denied") return "Refused: role not allowed";
  if (outcome === "disabled") return "Refused: server disabled";
  if (outcome === "unknown_tool") return "Refused: unknown tool";
  if (outcome === "pending_review") return "Refused: held for review (definition changed)";
  if (outcome === "unreachable") return `${titleCase(w.server)} unreachable`;
  if (outcome === "timeout") return error && error.includes("OUTCOME UNKNOWN") ? "Timed out. Check before retrying" : `${titleCase(w.server)} timed out`;
  if (outcome === "auth_failed") return `${titleCase(w.server)} rejected the stored credential`;
  if (outcome === "tool_error") return `${w.verb} ${w.argument || w.noun} failed`;
  if (outcome === "aborted") return "Aborted before it finished";
  return `${w.verb} ${w.argument || w.noun}`;
}

export const titleCase = (s: string) => (s ? s[0]!.toUpperCase() + s.slice(1) : s);

export type Tone = "success" | "danger" | "warning" | "neutral";

export function outcomeTone(outcome: string, error: string | null): { label: string; tone: Tone } {
  switch (outcome) {
    case "ok":
      return { label: "Success", tone: "success" };
    case "denied":
    case "disabled":
    case "unknown_tool":
    case "pending_review":
      return { label: "Refused", tone: "danger" };
    case "timeout":
      return error && error.includes("OUTCOME UNKNOWN") ? { label: "Outcome unknown", tone: "warning" } : { label: "Timed out", tone: "danger" };
    case "aborted":
      return { label: "Aborted", tone: "warning" };
    case "unreachable":
    case "auth_failed":
    case "protocol":
    case "tool_error":
      return { label: "Failed", tone: "danger" };
    default:
      return { label: titleCase(outcome), tone: "neutral" };
  }
}

/** Short wording of the gateway's server health for cards and badges. */
export function healthWords(s: { enabled: boolean; health: string; status: string }): { label: string; tone: Tone | "checking" } {
  if (!s.enabled) return { label: "Disabled", tone: "neutral" };
  if (s.health === "ok") return { label: "Connected", tone: "success" };
  if (s.health === "down") return { label: "Unreachable", tone: "danger" };
  if (s.health === "auth_failed") return { label: "Credential rejected", tone: "danger" };
  return { label: "Checking", tone: "checking" };
}
