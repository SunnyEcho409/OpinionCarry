import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchMarkets, type MarketItem, type MarketsResponse } from "./api";

const AUTO_REFRESH_MS = 30_000;
const PAGE_SIZE = 20;

type PageToken = number | "ellipsis";

function formatCountdown(seconds: number | null): string {
  if (seconds === null) return "n/a";
  const safe = Math.max(0, Math.floor(seconds));
  const days = Math.floor(safe / 86_400);
  const hours = Math.floor((safe % 86_400) / 3_600);
  const minutes = Math.floor((safe % 3_600) / 60);
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  return `${hours}h ${minutes}m`;
}

function normalizeProbability(value: number | null): number | null {
  if (value === null || Number.isNaN(value)) return null;
  if (value >= 0 && value <= 1) return value;
  if (value >= 0 && value <= 100) return value / 100;
  return null;
}

function formatPercent(value: number | null): string {
  const probability = normalizeProbability(value);
  if (probability === null) return "n/a";
  const percent = probability * 100;
  return `${percent >= 10 ? percent.toFixed(0) : percent.toFixed(1)}%`;
}

function invertProbability(value: number | null): number | null {
  const probability = normalizeProbability(value);
  if (probability === null) return null;
  return 1 - probability;
}

type MarketOutcome = MarketItem["outcome_prices"][number];

function getOutcomeValues(row: MarketOutcome): {
  yesValue: number | null;
  noValue: number | null;
  mainValue: number | null;
} {
  const yesValue = row.yes_price ?? row.bid_price ?? row.price ?? invertProbability(row.no_price);
  const noValue = row.no_price ?? invertProbability(row.yes_price ?? row.bid_price ?? row.price);
  const mainValue = yesValue ?? invertProbability(noValue);

  return { yesValue, noValue, mainValue };
}

function roundedPercent(value: number | null): number | null {
  const probability = normalizeProbability(value);
  if (probability === null) return null;
  const percent = probability * 100;
  return percent >= 10 ? Math.round(percent) : Math.round(percent * 10) / 10;
}

function parsePercentFilter(input: string): number | null {
  const raw = input.trim().replace(",", ".");
  if (raw.length === 0) return null;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) return null;
  return parsed >= 10 ? Math.round(parsed) : Math.round(parsed * 10) / 10;
}

function formatFilterPercent(value: number): string {
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}%`;
}

function getOutcomePercentMatch(
  row: MarketOutcome,
  targetPercent: number | null
): { rowMatch: boolean; yesMatch: boolean; noMatch: boolean; mainMatch: boolean } {
  if (targetPercent === null) {
    return { rowMatch: false, yesMatch: false, noMatch: false, mainMatch: false };
  }

  const { yesValue, noValue, mainValue } = getOutcomeValues(row);
  const yesMatch = roundedPercent(yesValue) === targetPercent;
  const noMatch = roundedPercent(noValue) === targetPercent;
  const mainMatch = roundedPercent(mainValue) === targetPercent;
  const rowMatch = yesMatch || noMatch || mainMatch;

  return { rowMatch, yesMatch, noMatch, mainMatch };
}

function itemHasMatchingOutcomePercent(item: MarketItem, targetPercent: number | null): boolean {
  if (targetPercent === null) return true;
  return item.outcome_prices.some((row) => getOutcomePercentMatch(row, targetPercent).rowMatch);
}

function hasPrice(item: MarketItem): boolean {
  return item.outcome_prices.some((row) => {
    const values = [row.yes_price, row.no_price, row.bid_price, row.ask_price, row.price];
    return values.some((value) => value !== null && !Number.isNaN(value));
  });
}

function buildPageTokens(totalPages: number, currentPage: number): PageToken[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const tokens: PageToken[] = [1];
  const left = Math.max(2, currentPage - 1);
  const right = Math.min(totalPages - 1, currentPage + 1);

  if (left > 2) tokens.push("ellipsis");
  for (let page = left; page <= right; page += 1) {
    tokens.push(page);
  }
  if (right < totalPages - 1) tokens.push("ellipsis");

  tokens.push(totalPages);
  return tokens;
}

function marketLink(item: MarketItem): string | null {
  if (!item.url_slug) return null;
  return `https://app.opinion.trade/market/${item.url_slug}`;
}

function IncentiveBadge() {
  return (
    <span className="gift-badge" title="This market has incentive factor" aria-label="Has incentive factor">
      <svg viewBox="0 0 24 24" role="img" aria-hidden="true">
        <rect x="4" y="11" width="16" height="9" rx="1.8" />
        <path d="M12 11V20" />
        <path d="M4 11H20" />
        <path d="M12 11H7.8C6.8 11 6 10.2 6 9.2C6 8.2 6.8 7.4 7.8 7.4C10 7.4 12 11 12 11Z" />
        <path d="M12 11H16.2C17.2 11 18 10.2 18 9.2C18 8.2 17.2 7.4 16.2 7.4C14 7.4 12 11 12 11Z" />
      </svg>
    </span>
  );
}

type AsciiOffset = { x: -1 | 0 | 1; y: -1 | 0 | 1 };

function buildAsciiFace(offset: AsciiOffset, blink: boolean): string {
  const makeEye = (x: AsciiOffset["x"], y: AsciiOffset["y"]): [string, string, string] => {
    if (blink) return ["(- -)", "(- -)", "(- -)"];

    const pupilColumn = x + 1;
    const pupilRow = y + 1;
    const rows = [0, 1, 2].map((row) => {
      const inner = [0, 1, 2].map((column) => (row === pupilRow && column === pupilColumn ? "o" : " "));
      return `(${inner.join("")})`;
    });

    return [rows[0], rows[1], rows[2]];
  };

  const leftEye = makeEye(offset.x, offset.y);
  const rightEye = makeEye(offset.x, offset.y);

  return [
    "      .-''''''''''''''-.",
    "    .'  OPINION CARRY   '.",
    `   /    ${leftEye[0]}   ${rightEye[0]}    \\`,
    `  ;     ${leftEye[1]}   ${rightEye[1]}     ;`,
    `  |     ${leftEye[2]}   ${rightEye[2]}     |`,
    "  ;        .------.       ;",
    "   \\       |_____|      /",
    "    '._              _.'",
    "       '-.________.-'",
  ].join("\n");
}

function HeroAsciiWatcher() {
  const [offset, setOffset] = useState<AsciiOffset>({ x: 0, y: 0 });
  const [blink, setBlink] = useState(false);

  useEffect(() => {
    const handleMove = (event: MouseEvent) => {
      const normalizedX = (event.clientX / window.innerWidth) * 2 - 1;
      const normalizedY = (event.clientY / window.innerHeight) * 2 - 1;
      const nextX = Math.max(-1, Math.min(1, Math.round(normalizedX))) as AsciiOffset["x"];
      const nextY = Math.max(-1, Math.min(1, Math.round(normalizedY))) as AsciiOffset["y"];

      setOffset((previous) => (previous.x === nextX && previous.y === nextY ? previous : { x: nextX, y: nextY }));
    };

    window.addEventListener("mousemove", handleMove, { passive: true });
    return () => window.removeEventListener("mousemove", handleMove);
  }, []);

  useEffect(() => {
    let closeTimer = 0;
    const tick = window.setInterval(() => {
      setBlink(true);
      closeTimer = window.setTimeout(() => setBlink(false), 170);
    }, 2400);

    return () => {
      window.clearInterval(tick);
      window.clearTimeout(closeTimer);
    };
  }, []);

  const ascii = useMemo(() => buildAsciiFace(offset, blink), [offset, blink]);

  return (
    <pre className="hero-ascii" aria-hidden="true">
      {ascii}
    </pre>
  );
}

export default function App() {
  const [payload, setPayload] = useState<MarketsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [query, setQuery] = useState("");
  const [priceFilterInput, setPriceFilterInput] = useState("");
  const [incentiveOnly, setIncentiveOnly] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);

  const load = useCallback(async (mode: "init" | "refresh") => {
    if (mode === "init") setIsLoading(true);

    try {
      const data = await fetchMarkets();
      setPayload(data);
    } catch (err) {
      console.error("Failed to fetch markets", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load("init");
    const refreshTimer = window.setInterval(() => {
      void load("refresh");
    }, AUTO_REFRESH_MS);

    return () => window.clearInterval(refreshTimer);
  }, [load]);

  const items = payload?.items ?? [];
  const priceFilterPercent = useMemo(() => parsePercentFilter(priceFilterInput), [priceFilterInput]);
  const isPriceFilterInvalid = priceFilterInput.trim().length > 0 && priceFilterPercent === null;

  const visibleItems = useMemo(() => {
    const q = query.trim().toLowerCase();

    const filtered = items.filter((item) => {
      const matchesQuery =
        q.length === 0 ||
        item.market_title.toLowerCase().includes(q) ||
        String(item.market_type ?? "").toLowerCase().includes(q) ||
        item.child_titles.some((title) => title.toLowerCase().includes(q));

      const matchesIncentive = !incentiveOnly || item.has_incentive_factor;
      const matchesPricePercent = itemHasMatchingOutcomePercent(item, priceFilterPercent);

      return matchesQuery && matchesIncentive && matchesPricePercent;
    });

    return filtered.sort((a, b) => {
      const aHasPrice = hasPrice(a);
      const bHasPrice = hasPrice(b);
      if (aHasPrice !== bHasPrice) return aHasPrice ? -1 : 1;
      return 0;
    });
  }, [incentiveOnly, items, priceFilterPercent, query]);

  useEffect(() => {
    setCurrentPage(1);
  }, [query, incentiveOnly, priceFilterInput]);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil(visibleItems.length / PAGE_SIZE)),
    [visibleItems.length]
  );

  useEffect(() => {
    setCurrentPage((prev) => Math.min(prev, totalPages));
  }, [totalPages]);

  const pagedItems = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return visibleItems.slice(start, start + PAGE_SIZE);
  }, [currentPage, visibleItems]);

  const pageTokens = useMemo(
    () => buildPageTokens(totalPages, currentPage),
    [currentPage, totalPages]
  );

  return (
    <div className="page">
      <div className="noise-layer" />

      <header className="hero">
        <HeroAsciiWatcher />
        <div className="hero-content">
          <p className="eyebrow">
            <span>Opinion</span>
            <span>Hold</span>
            <span>Carry</span>
          </p>
          <h1 className="hero-title">
            <span className="hero-title-elegant">
              <span>Active</span>
              <span>markets,</span>
            </span>
            <span className="hero-title-tech">
              <span>for</span>
              <span>points</span>
              <span>farm</span>
            </span>
          </h1>
        </div>
      </header>

      <section className="controls" aria-label="Filters">
        <div className="controls-grid">
          <label className="field field--search">
            <span>Search</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Title, type, or child title"
            />
          </label>

          <label className="field field--price">
            <span>Outcome price</span>
            <div className={`price-input-wrap ${isPriceFilterInvalid ? "is-invalid" : ""}`.trim()}>
              <input
                value={priceFilterInput}
                onChange={(event) => setPriceFilterInput(event.target.value)}
                type="number"
                inputMode="decimal"
                min={0}
                max={100}
                step={0.1}
                placeholder="e.g. 28"
                aria-invalid={isPriceFilterInvalid || undefined}
              />
              <span className="price-input-suffix">%</span>
            </div>
          </label>

          <button
            type="button"
            className={`incentive-btn ${incentiveOnly ? "is-active" : ""}`.trim()}
            onClick={() => setIncentiveOnly((previous) => !previous)}
            aria-pressed={incentiveOnly}
          >
            Incentive markets only
          </button>
        </div>

        <div className="controls-meta">
          <p className={`controls-hint ${isPriceFilterInvalid ? "is-error" : ""}`.trim()}>
            {isPriceFilterInvalid
              ? "Enter a percent from 0 to 100."
              : priceFilterPercent === null
                ? "Set outcome % to show markets where any outcome has this value."
                : `Showing markets with at least one outcome at ${formatFilterPercent(priceFilterPercent)}.`}
          </p>
          {priceFilterInput.trim().length > 0 ? (
            <button
              type="button"
              className="filter-reset-btn"
              onClick={() => setPriceFilterInput("")}
            >
              Reset price
            </button>
          ) : null}
        </div>
      </section>

      {isLoading ? (
        <div className="panel">Loading markets...</div>
      ) : visibleItems.length === 0 ? (
        <div className="panel">No markets match your current filters.</div>
      ) : (
        <>
          <section className="grid" aria-label="Market cards">
            {pagedItems.map((item) => {
              const link = marketLink(item);
              const imageUrl = item.cover_url || item.thumbnail_url;
              const fallbackInitial = item.market_title.trim().charAt(0).toUpperCase() || "?";
              const cardClass = `market-card ${item.has_incentive_factor ? "market-card--incentive" : ""}`.trim();
              const cardContent = (
                <>
                  <div className="market-head">
                    <div className="market-head-main">
                      {imageUrl ? (
                        <img
                          className="market-thumb"
                          src={imageUrl}
                          alt={item.market_title}
                          loading="lazy"
                          decoding="async"
                        />
                      ) : (
                        <div className="market-thumb market-thumb--empty" aria-hidden="true">
                          {fallbackInitial}
                        </div>
                      )}
                      <p className="market-title">{item.market_title}</p>
                    </div>
                    <div className="market-head-side">
                      {item.has_incentive_factor ? <IncentiveBadge /> : null}
                    </div>
                  </div>

                  <div className="market-metrics">
                    <span>
                      Type: <strong>{item.market_type ?? "n/a"}</strong>
                    </span>
                  <span>
                    Time left: <strong>{formatCountdown(item.seconds_to_expiry)}</strong>
                  </span>
                </div>

                  {item.outcome_prices.length > 0 ? (
                    <div className="outcomes-wrap">
                      <p className="cutoff">Outcomes</p>
                      <div className="outcomes-scroll">
                        {item.outcome_prices.map((row) => {
                          const yesLabel = row.yes_label?.trim() ? row.yes_label : "YES";
                          const noLabel = row.no_label?.trim() ? row.no_label : "NO";
                          const { yesValue, noValue, mainValue } = getOutcomeValues(row);
                          const match = getOutcomePercentMatch(row, priceFilterPercent);

                          return (
                            <div
                              key={`${item.market_id}-${row.token_id}`}
                              className={`outcome-row ${match.rowMatch ? "outcome-row--match" : ""}`.trim()}
                            >
                              <span className={`outcome-label ${match.rowMatch ? "outcome-label--match" : ""}`.trim()}>
                                {row.outcome_label}
                              </span>
                              {!row.is_resolved ? (
                                <span className={`outcome-percent ${match.mainMatch ? "outcome-percent--match" : ""}`.trim()}>
                                  {formatPercent(mainValue)}
                                </span>
                              ) : null}
                              <div className="outcome-actions">
                                {row.is_resolved ? (
                                  <span className="outcome-chip outcome-chip--resolved">
                                    Resolved: {row.resolved_label ?? "Resolved"}
                                  </span>
                                ) : (
                                  <>
                                    <span className={`outcome-chip outcome-chip--yes ${match.yesMatch ? "outcome-chip--match" : ""}`.trim()}>
                                      {yesLabel} {formatPercent(yesValue)}
                                    </span>
                                    <span className={`outcome-chip outcome-chip--no ${match.noMatch ? "outcome-chip--match" : ""}`.trim()}>
                                      {noLabel} {formatPercent(noValue)}
                                    </span>
                                  </>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : (
                    <div className="outcomes-wrap">
                      <p className="cutoff">Outcomes</p>
                      <div className="outcomes-empty">No outcomes available</div>
                    </div>
                  )}
                </>
              );

              if (link) {
                return (
                  <a
                    key={item.market_id}
                    href={link}
                    target="_blank"
                    rel="noreferrer"
                    className={`${cardClass} market-card-link`}
                  >
                    {cardContent}
                  </a>
                );
              }

              return (
                <article key={item.market_id} className={cardClass}>
                  {cardContent}
                </article>
              );
            })}
          </section>
          <section className="pagination" aria-label="Pagination">
            <div className="pagination-controls">
              <button
                className="pagination-btn"
                onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                disabled={currentPage <= 1}
              >
                Prev
              </button>
              <div className="pagination-pages">
                {pageTokens.map((token, index) =>
                  token === "ellipsis" ? (
                    <span key={`ellipsis-${index}`} className="pagination-ellipsis">
                      •••
                    </span>
                  ) : (
                    <button
                      key={token}
                      className={`pagination-page-btn ${token === currentPage ? "is-active" : ""}`}
                      onClick={() => setCurrentPage(token)}
                      aria-label={`Go to page ${token}`}
                      aria-current={token === currentPage ? "page" : undefined}
                    >
                      {token}
                    </button>
                  )
                )}
              </div>
              <button
                className="pagination-btn"
                onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))}
                disabled={currentPage >= totalPages}
              >
                Next
              </button>
            </div>
            <p className="pagination-info">
              Page {currentPage} / {totalPages} · {visibleItems.length} markets
            </p>
          </section>
        </>
      )}
    </div>
  );
}
