export interface RecentActivity {
  value: string;
  label: string;
  detail?: string;
  at: string;
}

const SEARCH_KEY = "octopus:v2.1:recent-searches";
const OPEN_KEY = "octopus:v2.1:recent-opens";

function read(key: string): RecentActivity[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) ?? "[]") as RecentActivity[];
    return Array.isArray(value) ? value.slice(0, 6) : [];
  } catch {
    return [];
  }
}

function write(key: string, item: RecentActivity): void {
  try {
    const next = [item, ...read(key).filter((value) => value.value !== item.value)].slice(0, 6);
    window.localStorage.setItem(key, JSON.stringify(next));
  } catch {
    // Activity history is optional and must not block research work.
  }
}

export const recentActivity = {
  searches: () => read(SEARCH_KEY),
  opens: () => read(OPEN_KEY),
  recordSearch: (query: string) => write(SEARCH_KEY, {
    value: query,
    label: query,
    at: new Date().toISOString(),
  }),
  recordOpen: (documentId: string, label: string, detail: string) => write(OPEN_KEY, {
    value: documentId,
    label,
    detail,
    at: new Date().toISOString(),
  }),
};
