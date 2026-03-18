-- trade_outcomes table — tracks whether alerted cards hit TP1/TP2/Stop
-- Run this in Supabase SQL Editor (Dashboard → SQL → New query)

CREATE TABLE IF NOT EXISTS trade_outcomes (
    id          BIGSERIAL PRIMARY KEY,
    alert_id    BIGINT REFERENCES alerts(id) ON DELETE CASCADE,
    trade_date  DATE NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL DEFAULT 'long',
    session     TEXT DEFAULT '',
    entry_price NUMERIC(12,4),
    stop_price  NUMERIC(12,4),
    tp1_price   NUMERIC(12,4),
    tp2_price   NUMERIC(12,4),
    status      TEXT NOT NULL DEFAULT 'open',
    exit_price  NUMERIC(12,4),
    pnl_pct     NUMERIC(8,2),
    hit_at      TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_outcomes_date_status
    ON trade_outcomes (trade_date, status);

CREATE INDEX IF NOT EXISTS idx_outcomes_alert_id
    ON trade_outcomes (alert_id);

-- RLS: allow service-role key full access (same pattern as alerts table)
ALTER TABLE trade_outcomes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for service role" ON trade_outcomes
    FOR ALL
    USING (true)
    WITH CHECK (true);
