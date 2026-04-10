import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const fetchMock = vi.fn((input: RequestInfo | URL) => {
  const url = String(input);

  if (url.endsWith("/health")) {
    return Promise.resolve({
      ok: true,
      json: async () => ({
        status: "ok",
        app_env: "development",
        scheduler_enabled: false,
        scheduler_running: false,
      }),
    });
  }

  if (url.includes("/issuers/0001234567")) {
    return Promise.resolve({
      ok: true,
      json: async () => ({
        id: 1,
        cik: "0001234567",
        ticker: "ACME",
        name: "Acme Robotics, Inc.",
        exchange: null,
        sic: null,
        state_of_incorp: "DE",
        market_cap: "250000000.00",
        latest_price: "13.2500",
        filing_count: 3,
        transaction_count: 4,
        latest_signal_id: 1,
        latest_signal_score: "77.30",
        latest_signal_window_end: "2024-02-18",
        latest_signal_health_status: "caution",
        latest_signal_price_context_status: "available",
      }),
    });
  }

  if (url.includes("/results")) {
    return Promise.resolve({
      ok: true,
      json: async () => [
        {
          signal_id: 1,
          issuer_cik: "0001234567",
          ticker: "ACME",
          issuer_name: "Acme Robotics, Inc.",
          first_seen_date: "2024-02-20",
          signal_score_at_mention: "77.30",
          is_active: true,
          first_seen_price: "10.0000",
          first_seen_price_date: "2024-02-20",
          first_seen_price_status: "captured",
          week_1_return_pct: "10.00",
          week_1_status: "captured",
          week_2_return_pct: "20.00",
          week_2_status: "captured",
          week_4_return_pct: "40.00",
          week_4_status: "captured",
          latest_completed_checkpoint: "week_4",
          latest_completed_return_pct: "40.00",
          best_return_pct: "40.00",
          worst_return_pct: "10.00",
          checkpoints: [
            {
              checkpoint_label: "first_seen",
              target_date: "2024-02-20",
              status: "captured",
              source: "static_daily",
              price_date: "2024-02-20",
              price_value: "10.0000",
              return_pct: null,
              details: {},
            },
            {
              checkpoint_label: "week_1",
              target_date: "2024-02-27",
              status: "captured",
              source: "static_daily",
              price_date: "2024-02-27",
              price_value: "11.0000",
              return_pct: "10.00",
              details: {},
            },
            {
              checkpoint_label: "week_2",
              target_date: "2024-03-05",
              status: "captured",
              source: "static_daily",
              price_date: "2024-03-05",
              price_value: "12.0000",
              return_pct: "20.00",
              details: {},
            },
            {
              checkpoint_label: "week_4",
              target_date: "2024-03-19",
              status: "captured",
              source: "static_daily",
              price_date: "2024-03-19",
              price_value: "14.0000",
              return_pct: "40.00",
              details: {},
            },
          ],
        },
      ],
    });
  }

  if (url.includes("/signals/") && !url.endsWith("/signals/latest")) {
    return Promise.resolve({
      ok: true,
      json: async () => ({
        id: 1,
        issuer_cik: "0001234567",
        ticker: "ACME",
        issuer_name: "Acme Robotics, Inc.",
        window_start: "2024-02-14",
        window_end: "2024-02-18",
        unique_buyers: 2,
        total_purchase_usd: "259800.00",
        average_purchase_usd: "129900.00",
        signal_score: "77.30",
        latest_transaction_date: "2024-02-18",
        transaction_count: 2,
        first_time_buyer_count: 2,
        includes_indirect: false,
        includes_amendment: true,
        health_status: "unknown",
        price_context_status: "unavailable",
        summary_status: "generated",
        explanation: "2 unique insiders bought $259,800.00 of ACME over 5 days.",
        component_breakdown: {
          cluster_strength: {
            status: "available",
            raw_score: 18.66,
            max_score: 30,
            reweighted_score: 33.93,
            details: {
              buyers_points: 6,
              transaction_points: 4,
              total_value_points: 8.66,
            },
          },
          event_context: {
            status: "available",
            raw_score: 10,
            max_score: 10,
            reweighted_score: 10,
            details: {
              recent_filing_count: 4,
              earnings_related_points: 4,
            },
          },
        },
        ai_summary: {
          text: "Public filings show coordinated insider buying in ACME during the current window.",
          highlights: ["2 insiders bought shares", "Signal score 77.30"],
          warnings: ["Uses public SEC filings only.", "Not investment advice."],
          provider: "static",
          model: "static-facts",
          generated_at: "2026-04-08T10:00:00+00:00",
        },
        alerts: [
          {
            id: 11,
            channel: "webhook",
            status: "sent",
            sent_at: "2026-04-08T10:05:00+00:00",
            event_type: "new_signal",
            reason: "Signal reached 77.30 with 2 unique buyers and $259800.00 of purchases.",
            score_at_send: "77.30",
            total_purchase_usd_at_send: "259800.00",
            unique_buyers_at_send: 2,
          },
        ],
        trade_setup: {
          setup_label: "review_only_pullback_plan",
          entry_zone_low: 11.9,
          entry_zone_high: 13,
          cluster_purchase_vwap: 12.37,
          swing_low_reference: 10.24,
          protective_stop: 9.93,
          risk_to_stop_pct: 5.43,
          latest_price_source: "alpha_vantage",
          disclaimer: "Review setup only. Public filings can lag the actual transaction and this is not investment advice.",
        },
        qualifying_transactions: [
          {
            transaction_id: 2,
            accession_number: "0001234567-24-000002",
            filing_url: "https://example.com/filing",
            xml_url: "https://example.com/xml",
            insider_id: 101,
            insider_name: "Jane Example",
            insider_role: "Director",
            transaction_date: "2024-02-14",
            security_title: "Common Stock",
            shares: "12000.0000",
            price_per_share: "11.9000",
            value_usd: "142800.00",
            ownership_type: "D",
          },
        ],
      }),
    });
  }

  if (url.includes("/signals")) {
    return Promise.resolve({
      ok: true,
      json: async () => [
        {
          id: 1,
          issuer_cik: "0001234567",
          ticker: "ACME",
          issuer_name: "Acme Robotics, Inc.",
          window_start: "2024-02-14",
          window_end: "2024-02-18",
          unique_buyers: 2,
          total_purchase_usd: "259800.00",
          average_purchase_usd: "129900.00",
          signal_score: "77.30",
          latest_transaction_date: "2024-02-18",
          transaction_count: 2,
          first_time_buyer_count: 2,
          includes_indirect: false,
          includes_amendment: true,
          health_status: "unknown",
          price_context_status: "unavailable",
          summary_status: "generated",
          explanation: "2 unique insiders bought $259,800.00 of ACME over 5 days.",
          component_breakdown: {
            cluster_strength: {
              status: "available",
              raw_score: 18.66,
              max_score: 30,
              reweighted_score: 33.93,
              details: {
                buyers_points: 6,
                transaction_points: 4,
                total_value_points: 8.66,
              },
            },
            event_context: {
              status: "available",
              raw_score: 10,
              max_score: 10,
              reweighted_score: 10,
              details: {},
            },
          },
        },
      ],
    });
  }

  return Promise.reject(new Error(`Unhandled fetch URL: ${url}`));
});

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockClear();
  });

  it("renders the simplified board and opens centered detail content", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^SECTOR4$/i })).toBeInTheDocument();
    });

    expect(screen.getByText(/^Ranked insider clusters$/i)).toBeInTheDocument();
    expect(screen.getByText(/^Lead signal$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Ticker search/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Minimum score/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Minimum buyers/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/77.3/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /Open details/i }));

    await waitFor(() => {
      expect(screen.getByText(/Review setup/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Public filings show coordinated insider buying/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Evidence/i }));

    await waitFor(() => {
      expect(screen.getByText(/Jane Example/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Signal reached 77.30/i)).toBeInTheDocument();
    expect(screen.getByText(/Open filing/i)).toBeInTheDocument();
    expect(screen.getByText(/Open XML/i)).toBeInTheDocument();
  });

  it("applies ticker-only search with the hidden default filters", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/^Ranked insider clusters$/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Ticker search/i), {
      target: { value: "acme" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Search$/i }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([request]) =>
          String(request).includes(
            "/signals?ticker=ACME&include_unknown_health=true&include_indirect=false&include_amendments=true",
          ),
        ),
      ).toBe(true);
    });

    expect(screen.getByText(/Ticker ACME/i)).toBeInTheDocument();
    expect(screen.queryByText(/Default scanner profile/i)).not.toBeInTheDocument();
  });

  it("shows the forward return results view", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Results/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Results/i }));

    await waitFor(() => {
      expect(screen.getByText(/^Forward return tracker$/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/SECTOR4 now logs the first seen market price/i)).toBeInTheDocument();
    expect(screen.getByText(/Tracked signals/i)).toBeInTheDocument();
    expect(screen.getAllByText(/\$11.00/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\$12.00/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\$14.00/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\+10.00%/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\+40.00%/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/4 weeks/i).length).toBeGreaterThan(0);
  });
});


