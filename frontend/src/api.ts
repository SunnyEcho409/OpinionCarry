export type OutcomePriceItem = {
  outcome_id: number | null;
  outcome_label: string;
  yes_label: string | null;
  no_label: string | null;
  outcome_status: number | null;
  outcome_status_enum: string | null;
  is_resolved: boolean;
  resolved_label: string | null;
  token_id: string;
  price: number | null;
  bid_price: number | null;
  ask_price: number | null;
  yes_price: number | null;
  no_price: number | null;
};

export type MarketItem = {
  market_id: number;
  market_title: string;
  market_type: number | null;
  status: number | null;
  status_enum: string | null;
  thumbnail_url: string | null;
  cover_url: string | null;
  cutoff_at: string | null;
  seconds_to_expiry: number | null;
  url_slug: string | null;
  has_incentive_factor: boolean;
  outcome_prices: OutcomePriceItem[];
  child_markets: number;
  child_titles: string[];
};

export type MarketsResponse = {
  count: number;
  items: MarketItem[];
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchMarkets(limit = 1000): Promise<MarketsResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: "0", only_active: "true" });
  const url = `${API_BASE_URL}/markets?${params.toString()}`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }
  return (await response.json()) as MarketsResponse;
}
